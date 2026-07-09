from types import SimpleNamespace

import pytest

from execution.orchestration.dispatch_payload import (
    DispatchPayloadValidationError,
    resolve_dispatch_payload_refs,
)
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


def test_resolver_returns_only_explicit_attachment_refs_supported_by_agent_card():
    attachment = UserAttachment(
        file_id="file-1",
        s3_key="uploads/room-1/file-1/report.pdf",
        mime_type="application/pdf",
        file_name="report.pdf",
        size_bytes=16,
    )
    payload = resolve_dispatch_payload_refs(
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


def test_resolver_rejects_unreferenced_original_attachment():
    attachment = UserAttachment(
        file_id="file-1",
        s3_key="uploads/room-1/file-1/report.pdf",
        mime_type="application/pdf",
        file_name="report.pdf",
        size_bytes=16,
    )
    payload = resolve_dispatch_payload_refs(
        run_state=_state(),
        target_agent_card=SimpleNamespace(default_input_modes=["application/pdf"]),
        context_refs=[],
        artifact_refs=[],
        attachment_refs=[],
        original_attachments=[attachment],
    )

    assert payload.selected_attachment_refs == []
    assert payload.attachment_failures == []


def test_resolver_returns_failure_for_incompatible_explicit_attachment_ref():
    attachment = UserAttachment(
        file_id="file-1",
        s3_key="uploads/room-1/file-1/report.pdf",
        mime_type="application/pdf",
        file_name="report.pdf",
        size_bytes=16,
    )
    payload = resolve_dispatch_payload_refs(
        run_state=_state(),
        target_agent_card=SimpleNamespace(default_input_modes=["text"]),
        context_refs=[],
        artifact_refs=[],
        attachment_refs=[
            DispatchContentRef(kind=DispatchRefKind.ATTACHMENT, ref_id="file-1")
        ],
        original_attachments=[attachment],
    )

    assert payload.selected_attachment_refs == []
    assert payload.attachment_failures == [
        {
            "ref_id": "file-1",
            "code": "agent_does_not_accept_file_type",
            "message": "Agent does not accept report.pdf (application/pdf).",
        }
    ]


def test_resolver_rejects_unknown_required_artifact_ref():
    with pytest.raises(DispatchPayloadValidationError, match="unknown artifact"):
        resolve_dispatch_payload_refs(
            run_state=_state(),
            target_agent_card=SimpleNamespace(default_input_modes=["text"]),
            context_refs=[],
            artifact_refs=[
                DispatchContentRef(kind=DispatchRefKind.ARTIFACT, ref_id="missing")
            ],
            attachment_refs=[],
            original_attachments=[],
        )
