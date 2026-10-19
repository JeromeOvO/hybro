"""PDF attachment text projection used by the orchestrator resource tools."""

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from conversation.attachment_projection import (
    AttachmentProjectionService,
    attachment_resource_ref_id,
    canonical_content_fingerprint,
    text_projection_ref_id,
)
from models.room import UserAttachment


def _pdf_attachment(file_id: str = "file-1") -> UserAttachment:
    return UserAttachment(
        file_id=file_id,
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
        "file-1",
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
async def test_pdf_projection_extracts_text_and_metadata():
    data = _text_pdf_bytes("Insured revenue is 50M")
    attachment = _pdf_attachment()
    attachment.size_bytes = len(data)
    service = AttachmentProjectionService(
        content_reader=SimpleNamespace(get_bytes=AsyncMock(return_value=data))
    )

    projection, payload = await service.ensure_projection(attachment)

    assert projection.status == "ready"
    assert projection.ref_id == text_projection_ref_id("file-1")
    assert projection.source_ref_id == attachment_resource_ref_id("file-1")
    assert payload is not None
    assert "Insured revenue is 50M" in payload.text
    assert payload.content_fingerprint == canonical_content_fingerprint(payload.text)
    assert payload.metadata["page_count"] == 1
    assert payload.metadata["is_truncated"] is False


@pytest.mark.asyncio
async def test_pdf_projection_truncates_to_the_text_limit():
    data = _text_pdf_bytes("Insured revenue is 50M")
    attachment = _pdf_attachment()
    attachment.size_bytes = len(data)
    service = AttachmentProjectionService(
        content_reader=SimpleNamespace(get_bytes=AsyncMock(return_value=data)),
        max_text_chars=7,
    )

    _projection, payload = await service.ensure_projection(attachment)

    assert payload is not None
    assert payload.text == "Insured"
    assert payload.metadata["is_truncated"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mime_type", "target_mime", "reason"),
    [
        ("image/png", "text/plain", "unsupported_projection_source_mime"),
        ("application/pdf", "text/markdown", "unsupported_projection_mime"),
    ],
)
async def test_projection_rejects_unsupported_mime(mime_type, target_mime, reason):
    attachment = _pdf_attachment()
    attachment.mime_type = mime_type
    reader = SimpleNamespace(get_bytes=AsyncMock())
    service = AttachmentProjectionService(content_reader=reader)

    projection, payload = await service.ensure_projection(
        attachment, target_mime=target_mime
    )

    assert payload is None
    assert projection.status == "failed"
    assert projection.failure_reason == reason
    reader.get_bytes.assert_not_awaited()
