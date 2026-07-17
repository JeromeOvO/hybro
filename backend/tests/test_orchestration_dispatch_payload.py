from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from execution.orchestration.dispatch_payload import (
    DispatchPayloadValidationError,
    resolve_dispatch_payload_refs,
)
from execution.orchestration.resources import ResourcePayload
from models.orchestration import (
    DispatchContentRef,
    DispatchRefKind,
    OrchestrationRunState,
)
from models.room import UserAttachment


def _state():
    return OrchestrationRunState(
        run_id="run-1",
        room_id="room-1",
        user_message_id="user-msg-1",
        goal="Underwrite the submission",
        candidate_agent_ids=["agent-1"],
        artifacts=[
            {
                "artifact_key": "broker-msg:artifact_id:submission",
                "name": "Broker submission",
                "mime_type": "application/json",
                "summary": "Structured broker submission.",
            }
        ],
    )


@pytest.mark.asyncio
async def test_resolver_returns_only_explicit_attachment_refs_supported_by_agent_card():
    attachment = UserAttachment(
        file_id="file-1",
        s3_key="uploads/room-1/file-1/report.pdf",
        mime_type="application/pdf",
        file_name="report.pdf",
        size_bytes=16,
    )
    payload = await resolve_dispatch_payload_refs(
        run_state=_state(),
        target_agent_card=SimpleNamespace(default_input_modes=["application/pdf"]),
        context_refs=[],
        artifact_refs=[],
        attachment_refs=[
            DispatchContentRef(kind=DispatchRefKind.ATTACHMENT, ref_id="file-1")
        ],
        original_attachments=[attachment],
    )

    assert payload.selected_attachment_refs == ["file-1"]
    assert payload.selected_artifact_refs == []
    assert payload.attachment_failures == []


@pytest.mark.asyncio
async def test_resolver_rejects_unreferenced_original_attachment():
    attachment = UserAttachment(
        file_id="file-1",
        s3_key="uploads/room-1/file-1/report.pdf",
        mime_type="application/pdf",
        file_name="report.pdf",
        size_bytes=16,
    )
    payload = await resolve_dispatch_payload_refs(
        run_state=_state(),
        target_agent_card=SimpleNamespace(default_input_modes=["application/pdf"]),
        context_refs=[],
        artifact_refs=[],
        attachment_refs=[],
        original_attachments=[attachment],
    )

    assert payload.selected_attachment_refs == []
    assert payload.attachment_failures == []


@pytest.mark.asyncio
async def test_resolver_rejects_incompatible_explicit_attachment_ref():
    attachment = UserAttachment(
        file_id="file-1",
        s3_key="uploads/room-1/file-1/report.pdf",
        mime_type="application/pdf",
        file_name="report.pdf",
        size_bytes=16,
    )
    with pytest.raises(
        DispatchPayloadValidationError,
        match="Agent does not accept report.pdf",
    ) as exc_info:
        await resolve_dispatch_payload_refs(
            run_state=_state(),
            target_agent_card=SimpleNamespace(default_input_modes=["text"]),
            context_refs=[],
            artifact_refs=[],
            attachment_refs=[
                DispatchContentRef(kind=DispatchRefKind.ATTACHMENT, ref_id="file-1")
            ],
            original_attachments=[attachment],
        )

    assert exc_info.value.code == "agent_does_not_accept_file_type"


@pytest.mark.asyncio
async def test_resolver_rejects_unknown_required_artifact_ref():
    with pytest.raises(
        DispatchPayloadValidationError,
        match="unknown artifact",
    ) as exc_info:
        await resolve_dispatch_payload_refs(
            run_state=_state(),
            target_agent_card=SimpleNamespace(default_input_modes=["text"]),
            context_refs=[],
            artifact_refs=[
                DispatchContentRef(kind=DispatchRefKind.ARTIFACT, ref_id="missing")
            ],
            attachment_refs=[],
            original_attachments=[],
        )

    assert exc_info.value.code == "artifact_ref_not_found"


@pytest.mark.asyncio
async def test_resolver_rejects_unknown_required_context_ref():
    with pytest.raises(
        DispatchPayloadValidationError,
        match="Context ref not found",
    ) as exc_info:
        await resolve_dispatch_payload_refs(
            run_state=_state(),
            target_agent_card=SimpleNamespace(default_input_modes=["text"]),
            context_refs=[
                DispatchContentRef(
                    kind=DispatchRefKind.CONTEXT,
                    ref_id="missing",
                )
            ],
            artifact_refs=[],
            attachment_refs=[],
            original_attachments=[],
        )

    assert exc_info.value.code == "context_ref_not_found"


@pytest.mark.asyncio
async def test_resolver_rejects_unknown_required_attachment_ref():
    with pytest.raises(
        DispatchPayloadValidationError,
        match="Attachment ref not found: file:missing",
    ) as exc_info:
        await resolve_dispatch_payload_refs(
            run_state=_state(),
            target_agent_card=SimpleNamespace(default_input_modes=["text"]),
            context_refs=[],
            artifact_refs=[],
            attachment_refs=[
                DispatchContentRef(
                    kind=DispatchRefKind.ATTACHMENT,
                    ref_id="file:missing",
                )
            ],
            original_attachments=[],
        )

    assert exc_info.value.code == "attachment_ref_not_found"


@pytest.mark.asyncio
async def test_resolver_materializes_selected_projected_resource():
    provider = SimpleNamespace(
        resolve_ref=AsyncMock(
            return_value=ResourcePayload(
                ref_id="ctx:file-file-1:text",
                kind="context",
                mime_type="text/plain",
                text="Projected submission text",
            )
        )
    )

    payload = await resolve_dispatch_payload_refs(
        run_state=_state(),
        target_agent_card=SimpleNamespace(default_input_modes=["text"]),
        context_refs=[
            DispatchContentRef(
                kind=DispatchRefKind.CONTEXT,
                ref_id="ctx:file-file-1:text",
            )
        ],
        artifact_refs=[],
        attachment_refs=[],
        original_attachments=[],
        resource_provider=provider,
    )

    assert payload.selected_context_refs == ["ctx:file-file-1:text"]
    assert payload.resource_payloads[0].text == "Projected submission text"
    provider.resolve_ref.assert_awaited_once_with(
        "ctx:file-file-1:text",
        run_id="run-1",
        attachments=[],
    )
