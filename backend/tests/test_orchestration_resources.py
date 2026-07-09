from types import SimpleNamespace

import pytest

from execution.orchestration.resources import (
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
            summary="Text projection has not been generated.",
        )
    ]


@pytest.mark.asyncio
async def test_resource_provider_resolves_raw_attachment_metadata_payload():
    provider = OrchestrationResourceProvider()
    payload = await provider.resolve_ref(
        "file:file-1",
        attachments=[_pdf_attachment()],
    )

    assert payload == ResourcePayload(
        ref_id="file:file-1",
        kind="attachment",
        mime_type="application/pdf",
        text=None,
        summary="submission.pdf (application/pdf, 128 bytes)",
        metadata={
            "file_id": "file-1",
            "file_name": "submission.pdf",
            "s3_key": "uploads/room-1/file-1/submission.pdf",
            "size_bytes": 128,
        },
    )


def test_resource_ref_helpers_are_deterministic():
    assert attachment_resource_ref_id("file-1") == "file:file-1"
    assert text_projection_ref_id("file-1") == "ctx:file-file-1:text"
