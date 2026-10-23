"""Golden values for digests that are persisted and recomputed for comparison.

These hashes are stored in Mongo (``idempotency_fingerprint``, HITL
``route_fingerprint``, Agent-call ``resource_manifest.content_digest``) and
recomputed on retry, replay, or validation. Renaming a Python attribute must not
change them, so each value below is pinned to the hash produced before the
canonical payloads were decoupled from model attribute names.
"""

from __future__ import annotations

from common.dto import ExecutionRequest
from common.dto.hitl import HITLRouteSnapshot
from execution.idempotency import build_execution_request_fingerprint
from execution.orchestrator.a2a_runtime.models import (
    FrozenCallResourceRef,
    HITLRouteSnapshotV2,
)
from execution.orchestrator.a2a_runtime.resources import (
    _frozen_ref_digest_payload,
    freeze_call_manifest,
)
from execution.orchestrator.models import (
    PreparedResourceRef,
    RunResourceManifestSnapshot,
)

from ._orchestrator_a2a_helpers import binding

_V2_ROUTE_FIELDS = {
    "orchestration_run_id": "run-1",
    "call_record_id": "record-1",
    "invocation_id": "call-1",
    "conversation_id": "room-1",
    "room_epoch": 3,
    "binding_id": "binding-1",
    "agent_id": "agent-1",
    "interaction_revision": 2,
    "interaction_fingerprint": "fingerprint-1",
}

# Model field name -> stored canonical key. Adding a model field fails the guard
# tests below until it is deliberately added here and to the canonical payload.
# A renamed attribute maps to its unchanged stored key, e.g.
# "conversation_id": "room_id".
_V1_ROUTE_STORED_KEYS = {
    "schema_version": "schema_version",
    "route": "route",
    "orchestration_run_id": "orchestration_run_id",
    "task_id": "task_id",
    "context_id": "context_id",
    "continuation_message_id": "continuation_message_id",
    "agent_id": "agent_id",
}
_V2_ROUTE_STORED_KEYS = {
    "schema_version": "schema_version",
    "route": "route",
    "orchestration_run_id": "orchestration_run_id",
    "call_record_id": "call_record_id",
    "invocation_id": "invocation_id",
    "conversation_id": "room_id",
    "room_epoch": "room_epoch",
    "binding_id": "binding_id",
    "agent_id": "agent_id",
    "task_id": "task_id",
    "context_id": "context_id",
    "interaction_revision": "interaction_revision",
    "interaction_fingerprint": "interaction_fingerprint",
}
_FROZEN_REF_STORED_KEYS = {
    "ref_id": "ref_id",
    "kind": "kind",
    "conversation_id": "room_id",
    "room_epoch": "room_epoch",
    "source_message_id": "source_message_id",
    "source_agent_id": "source_agent_id",
    "mime_type": "mime_type",
    "size_bytes": "size_bytes",
    "content_digest": "content_digest",
    "projection_id": "projection_id",
    "materialization_digest": "materialization_digest",
}


def _assert_payload_covers_fields(
    model: object,
    payload: dict[str, object],
    stored_keys: dict[str, str],
) -> None:
    assert set(stored_keys) == set(type(model).model_fields)
    assert set(payload) == set(stored_keys.values())
    for field_name, stored_key in stored_keys.items():
        value = getattr(model, field_name)
        assert payload[stored_key] == getattr(value, "value", value)


def test_supervisor_route_payload_covers_every_model_field() -> None:
    snapshot = HITLRouteSnapshot(route="supervisor_run", orchestration_run_id="run-1")

    _assert_payload_covers_fields(
        snapshot, snapshot._fingerprint_payload(), _V1_ROUTE_STORED_KEYS
    )


def test_orchestrator_route_payload_covers_every_model_field() -> None:
    snapshot = HITLRouteSnapshotV2(
        **_V2_ROUTE_FIELDS, task_id="task-1", context_id="context-1"
    )

    _assert_payload_covers_fields(
        snapshot, snapshot._fingerprint_payload(), _V2_ROUTE_STORED_KEYS
    )


def test_frozen_ref_payload_covers_every_model_field() -> None:
    ref = FrozenCallResourceRef(
        ref_id="ref-1",
        kind="attachment",
        conversation_id="room-1",
        room_epoch=1,
        source_message_id="message-1",
        source_agent_id="agent-1",
        mime_type="application/pdf",
        size_bytes=10,
        content_digest="digest-1",
        projection_id="projection-1",
        materialization_digest="materialized-1",
    )

    _assert_payload_covers_fields(
        ref, _frozen_ref_digest_payload(ref), _FROZEN_REF_STORED_KEYS
    )


def test_execution_request_fingerprint_is_pinned() -> None:
    request = ExecutionRequest(
        conversation_id="room-1",
        sender_id="user-1",
        sender_name="Display Name",
        message={
            "room_id": "room-1",
            "message_id": "draft-1",
            "related_message_id": "parent-1",
            "message_content": {"message_text": "hello"},
            "quote": {
                "text": " quoted ",
                "source_message_id": "source-1",
                "source_kind": "agent",
                "sender_display_name": "Agent One",
                "source_agent_id": "agent-1",
            },
        },
        attachments=[{"file_id": "file-1"}],
        inline_file_ids=["file-2"],
        client_request_id="request-1",
        parent_message_id="parent-1",
        mode="supervisor",
        agent_scope={"source": "mention", "agent_ids": ["agent-2", "agent-1"]},
    )

    assert build_execution_request_fingerprint(request) == (
        "b08be579f42cf259c5126c75f160b66991a5183b2bbdf09134aaa7b3bf4f1e7e"
    )


def test_supervisor_route_snapshot_fingerprint_is_pinned() -> None:
    snapshot = HITLRouteSnapshot(route="supervisor_run", orchestration_run_id="run-1")

    assert snapshot.fingerprint == (
        "b4952cb648d9a380f0ecaddf4d73a5ab3dae7c3da7853f66f8954685bf2837b2"
    )


def test_orchestrator_route_snapshot_fingerprint_is_pinned() -> None:
    full = HITLRouteSnapshotV2(
        **_V2_ROUTE_FIELDS, task_id="task-1", context_id="context-1"
    )
    minimal = HITLRouteSnapshotV2(**_V2_ROUTE_FIELDS)

    assert full.fingerprint == (
        "daa4bf7da78765ea672b53d0bd29d6598263b7837a00df235457135e0316dc08"
    )
    assert minimal.fingerprint == (
        "0e02ecad61238f05560635a31033253f06915350a72b31a00015728935e9be5b"
    )


def test_frozen_call_resource_manifest_digest_is_pinned() -> None:
    run_manifest = RunResourceManifestSnapshot(
        manifest_id="run-resources",
        refs=[
            PreparedResourceRef(
                ref_id="attachment-1",
                kind="attachment",
                source_message_id="message-1",
                mime_type="application/pdf",
                size_bytes=100,
                content_digest="digest-1",
            )
        ],
        content_digest="run-digest",
    )
    frozen = freeze_call_manifest(
        arguments={"task": "review", "attachment_refs": ["attachment-1"]},
        run_manifest=run_manifest,
        binding=binding().model_copy(update={"input_modes": ["application/pdf"]}),
        source_conversation_id="room-1",
        source_room_epoch=4,
    )

    expected = "321a1b042a7d1f554b9de7af6e63e3d2b19d115c7e9445da19e2d2e8573835db"
    assert frozen.content_digest == expected
    assert frozen.manifest_id == f"call-resources-{expected}"
