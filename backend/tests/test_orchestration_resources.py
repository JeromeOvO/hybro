from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from execution.orchestration.outcome_evaluator import canonical_content_fingerprint
from execution.orchestration.resources import (
    AttachmentProjectionService,
    OrchestrationResourceProvider,
    ResourcePayload,
    ResourceProjectionRef,
    attachment_resource_ref_id,
    text_projection_ref_id,
)
from models.room import UserAttachment


def _pdf_attachment(file_id: str = "file-1") -> UserAttachment:
    return UserAttachment(
        file_id=file_id,
        s3_key=f"uploads/room-1/{file_id}/submission.pdf",
        mime_type="application/pdf",
        file_name="submission.pdf",
        size_bytes=128,
    )


def _minimal_pdf_bytes() -> bytes:
    buffer = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_metadata({"/Title": "Blank"})
    writer.write(buffer)
    return buffer.getvalue()


def _text_pdf_bytes(text: str) -> bytes:
    buffer = BytesIO()
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): writer._add_object(font)}
            )
        }
    )
    content = DecodedStreamObject()
    content.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode())
    page[NameObject("/Contents")] = writer._add_object(content)
    writer.write(buffer)
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_resource_provider_lists_pdf_attachment_with_text_projection_ref():
    provider = OrchestrationResourceProvider()
    resources = await provider.list_resources(
        run_id="run-1",
        room_id="room-1",
        user_message_id="msg-1",
        attachments=[_pdf_attachment()],
        candidate_agents=[
            SimpleNamespace(agent_id="text-agent", input_modes=["text"]),
            SimpleNamespace(agent_id="pdf-agent", input_modes=["application/pdf"]),
        ],
    )

    assert len(resources) == 1
    resource = resources[0]
    assert resource.ref_id == "file:file-1"
    assert resource.kind == "attachment"
    assert resource.origin == "user_message"
    assert resource.status == "ready"
    assert resource.content_fingerprint == canonical_content_fingerprint(
        {
            "file_id": "file-1",
            "s3_key": "uploads/room-1/file-1/submission.pdf",
            "size_bytes": 128,
            "mime_type": "application/pdf",
        }
    )
    assert resource.file_name == "submission.pdf"
    assert resource.mime_type == "application/pdf"
    assert resource.supported_by_agent_ids == ["pdf-agent"]
    assert resource.projections == [
        ResourceProjectionRef(
            ref_id="ctx:file-file-1:text",
            kind="context",
            source_ref_id="file:file-1",
            mime_type="text/plain",
            status="unavailable",
            recommended_for_input_modes=["text"],
            summary="Text projection service is unavailable.",
        )
    ]


@pytest.mark.asyncio
async def test_resource_provider_resolves_raw_attachment_metadata_payload():
    provider = OrchestrationResourceProvider()
    payload = await provider.resolve_ref(
        "file:file-1",
        run_id="run-1",
        attachments=[_pdf_attachment()],
    )

    assert payload == ResourcePayload(
        ref_id="file:file-1",
        kind="attachment",
        mime_type="application/pdf",
        text=None,
        content_fingerprint=canonical_content_fingerprint(
            {
                "file_id": "file-1",
                "s3_key": "uploads/room-1/file-1/submission.pdf",
                "size_bytes": 128,
                "mime_type": "application/pdf",
            }
        ),
        summary="submission.pdf (application/pdf, 128 bytes)",
        metadata={
            "file_id": "file-1",
            "file_name": "submission.pdf",
            "size_bytes": 128,
        },
    )


@pytest.mark.asyncio
async def test_projection_content_fingerprint_tracks_extracted_text():
    first_bytes = _text_pdf_bytes("Insured revenue is 50M")
    same_bytes = _text_pdf_bytes("Insured revenue is 50M")
    changed_bytes = _text_pdf_bytes("Insured revenue is 60M")
    attachment = _pdf_attachment()
    attachment.size_bytes = len(first_bytes)
    content_reader = SimpleNamespace(
        get_bytes=AsyncMock(side_effect=[first_bytes, same_bytes, changed_bytes])
    )
    service = AttachmentProjectionService(content_reader=content_reader)

    first_projection, first_payload = await service.ensure_projection(attachment)
    same_projection, same_payload = await service.ensure_projection(attachment)
    changed_projection, changed_payload = await service.ensure_projection(attachment)

    assert first_projection.content_fingerprint == same_projection.content_fingerprint
    assert first_payload is not None
    assert same_payload is not None
    assert changed_payload is not None
    assert first_payload.content_fingerprint == same_payload.content_fingerprint
    assert (
        first_projection.content_fingerprint != changed_projection.content_fingerprint
    )
    assert first_payload.content_fingerprint != changed_payload.content_fingerprint


@pytest.mark.asyncio
async def test_resource_provider_returns_none_for_unknown_ref():
    provider = OrchestrationResourceProvider()

    payload = await provider.resolve_ref(
        "file:missing",
        run_id="run-1",
        attachments=[_pdf_attachment()],
    )

    assert payload is None


def test_resource_ref_helpers_are_deterministic():
    assert attachment_resource_ref_id("file-1") == "file:file-1"
    assert text_projection_ref_id("file-1") == "ctx:file-file-1:text"


def test_attachment_projection_service_uses_public_default_text_limit():
    service = AttachmentProjectionService(content_reader=AsyncMock())

    assert service._max_text_chars == 120_000


@pytest.mark.asyncio
async def test_pdf_projection_failure_for_empty_text_pdf():
    content_reader = AsyncMock()
    content_reader.get_bytes = AsyncMock(return_value=_minimal_pdf_bytes())
    service = AttachmentProjectionService(content_reader=content_reader)

    projection, payload = await service.ensure_projection(_pdf_attachment())

    assert payload is None
    assert projection.ref_id == "ctx:file-file-1:text"
    assert projection.status == "failed"
    assert projection.failure_reason == "pdf_text_empty"
    content_reader.get_bytes.assert_awaited_once_with(
        "uploads/room-1/file-1/submission.pdf",
        max_bytes=10485760,
    )


@pytest.mark.asyncio
async def test_pdf_projection_failure_for_oversized_pdf_without_reading_bytes():
    content_reader = AsyncMock()
    content_reader.get_bytes = AsyncMock(return_value=b"")
    service = AttachmentProjectionService(
        content_reader=content_reader, max_pdf_bytes=10
    )
    attachment = _pdf_attachment()
    attachment.size_bytes = 11

    projection, payload = await service.ensure_projection(attachment)

    assert payload is None
    assert projection.status == "failed"
    assert projection.failure_reason == "pdf_projection_too_large"
    content_reader.get_bytes.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_caches_successful_projection_payload(monkeypatch):
    async def fake_projection(attachment, *, target_mime="text/plain"):
        projection = ResourceProjectionRef(
            ref_id="ctx:file-file-1:text",
            kind="context",
            source_ref_id="file:file-1",
            mime_type=target_mime,
            status="ready",
            recommended_for_input_modes=["text"],
            summary="Extracted 12 characters from 1 page.",
        )
        payload = ResourcePayload(
            ref_id="ctx:file-file-1:text",
            kind="context",
            mime_type="text/plain",
            text="hello world!",
            summary="Extracted 12 characters from 1 page.",
            metadata={
                "source_ref_id": "file:file-1",
                "char_count": 12,
                "is_truncated": False,
            },
        )
        return projection, payload

    projection_service = SimpleNamespace(ensure_projection=fake_projection)
    provider = OrchestrationResourceProvider(projection_service=projection_service)

    projection = await provider.ensure_projection(
        "file:file-1",
        run_id="run-1",
        attachments=[_pdf_attachment()],
    )
    payload = await provider.resolve_ref(
        "ctx:file-file-1:text",
        run_id="run-1",
        attachments=[_pdf_attachment()],
    )

    assert projection.status == "ready"
    assert payload.text == "hello world!"
    assert payload.metadata["is_truncated"] is False


@pytest.mark.asyncio
async def test_resource_catalog_generates_and_resolves_projection_without_preseed():
    projection_service = SimpleNamespace(
        ensure_projection=AsyncMock(
            return_value=(
                ResourceProjectionRef(
                    ref_id="ctx:file-file-1:text",
                    kind="context",
                    source_ref_id="file:file-1",
                    mime_type="text/plain",
                    status="ready",
                    recommended_for_input_modes=["text"],
                    summary="Projection ready.",
                ),
                ResourcePayload(
                    ref_id="ctx:file-file-1:text",
                    kind="context",
                    mime_type="text/plain",
                    text="projected text",
                ),
            )
        )
    )
    provider = OrchestrationResourceProvider(projection_service=projection_service)

    resources = await provider.list_resources(
        run_id="run-1",
        room_id="room-1",
        user_message_id="msg-1",
        attachments=[_pdf_attachment()],
        candidate_agents=[SimpleNamespace(agent_id="agent-1", input_modes=["text"])],
    )
    payload = await provider.resolve_ref(
        "ctx:file-file-1:text",
        run_id="run-1",
        attachments=[_pdf_attachment()],
    )

    assert resources[0].projections[0].status == "ready"
    assert payload is not None
    assert payload.text == "projected text"
    projection_service.ensure_projection.assert_awaited_once()


@pytest.mark.asyncio
async def test_resource_catalog_skips_projection_when_all_candidates_accept_pdf():
    projection_service = SimpleNamespace(ensure_projection=AsyncMock())
    provider = OrchestrationResourceProvider(projection_service=projection_service)

    resources = await provider.list_resources(
        run_id="run-1",
        room_id="room-1",
        user_message_id="msg-1",
        attachments=[_pdf_attachment()],
        candidate_agents=[
            SimpleNamespace(agent_id="pdf-agent", input_modes=["application/pdf"])
        ],
    )

    assert resources[0].projections == []
    projection_service.ensure_projection.assert_not_awaited()


@pytest.mark.asyncio
async def test_resource_provider_lazily_regenerates_projection_after_cache_miss():
    projection_service = SimpleNamespace(
        ensure_projection=AsyncMock(
            return_value=(
                ResourceProjectionRef(
                    ref_id="ctx:file-file-1:text",
                    kind="context",
                    source_ref_id="file:file-1",
                    mime_type="text/plain",
                    status="ready",
                    recommended_for_input_modes=["text"],
                ),
                ResourcePayload(
                    ref_id="ctx:file-file-1:text",
                    kind="context",
                    mime_type="text/plain",
                    text="regenerated text",
                ),
            )
        )
    )
    provider = OrchestrationResourceProvider(projection_service=projection_service)

    payload = await provider.resolve_ref(
        "ctx:file-file-1:text",
        run_id="run-1",
        attachments=[_pdf_attachment()],
    )

    assert payload is not None
    assert payload.text == "regenerated text"
    projection_service.ensure_projection.assert_awaited_once_with(
        _pdf_attachment(),
        target_mime="text/plain",
    )


@pytest.mark.asyncio
async def test_resource_provider_evicts_oldest_run_cache():
    provider = OrchestrationResourceProvider(max_cached_runs=1)

    await provider.list_resources(
        run_id="run-1",
        room_id="room-1",
        user_message_id="msg-1",
        attachments=[],
        candidate_agents=[],
    )
    await provider.list_resources(
        run_id="run-2",
        room_id="room-1",
        user_message_id="msg-1",
        attachments=[],
        candidate_agents=[],
    )

    assert list(provider._payloads_by_run) == ["run-2"]
    assert list(provider._projection_refs_by_run) == ["run-2"]
