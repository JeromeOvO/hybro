from __future__ import annotations

from types import UnionType
from typing import Annotated, get_args, get_origin

from common.dto.delivery import DeliveryEvent
from delivery.producer_policy import (
    ROOM_EVENT_PRODUCER_POLICY,
    canonical_processing_status_adapter,
)
from delivery.translator import to_sse_frame
from execution.orchestrator.public_text import enforce_public_label_policy
from tests._orchestrator_helpers import NOW


def _delivery_models() -> set[type]:
    annotated = get_args(DeliveryEvent)[0]
    assert get_origin(DeliveryEvent) is Annotated
    assert get_origin(annotated) is UnionType
    return set(get_args(annotated))


def test_every_top_level_room_event_producer_and_field_is_classified():
    assert set(ROOM_EVENT_PRODUCER_POLICY) == _delivery_models()
    for model, fields in ROOM_EVENT_PRODUCER_POLICY.items():
        assert set(fields) == set(model.model_fields), model.__name__
        assert all(fields.values()), model.__name__


def test_canonical_processing_status_adapter_has_exact_content_free_wire_shape():
    event = canonical_processing_status_adapter(
        room_id="room-1",
        user_message_id="user-1",
        client_request_id="request-1",
        status="processing",
    )
    frame = to_sse_frame(event, timestamp=NOW)
    assert frame["data"] == {
        "status": "processing",
        "message_id": "user-1",
        "related_message_id": "user-1",
        "client_request_id": "request-1",
        "details": None,
    }
    assert "agents" not in frame["data"]
    assert "agent_id" not in frame["data"]


def test_canonical_processing_status_adapter_rejects_missing_roots_and_status():
    import pytest

    with pytest.raises(ValueError, match="nonempty roots"):
        canonical_processing_status_adapter(
            room_id="room-1",
            user_message_id="",
            client_request_id="request-1",
            status="processing",
        )
    with pytest.raises(ValueError, match="allowlisted"):
        canonical_processing_status_adapter(
            room_id="room-1",
            user_message_id="user-1",
            client_request_id="request-1",
            status="error prose",
        )


def test_canonical_agent_and_tool_labels_enforce_configured_secret_policy():
    label = enforce_public_label_policy(
        "Weather token=hunter2 mongodb://user:pass@example.test/db",
        secret_values=("hunter2", "pass"),
    )
    assert "hunter2" not in label
    assert "pass@example" not in label
    assert label.count("[REDACTED]") >= 2


def test_canonical_content_families_are_sanitized_or_explicitly_prohibited():
    allowed_content_policies = {
        "intentional_content",
        "sanitized_text",
        "safe_summary",
        "private_legacy_only",
        "prohibited_canonical",
    }
    content_fields = {
        "details",
        "content_delta",
        "content",
        "task_content",
        "status_message",
        "parts",
        "artifact",
        "error",
        "reason",
        "prompt",
        "choices",
        "agent_label",
        "error_message",
        "payload",
    }
    for model, fields in ROOM_EVENT_PRODUCER_POLICY.items():
        for name in content_fields & fields.keys():
            assert fields[name] in allowed_content_policies, f"{model.__name__}.{name}"
