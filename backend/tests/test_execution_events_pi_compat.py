from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from common.dto import ProcessingStatusEvent
from execution.events import emit_processing_status, run_event_notification_from_payload


def _legacy_payload(event_type: str) -> dict:
    return {
        "event_id": f"event-{event_type}",
        "run_id": "message-1",
        "seq": 1,
        "type": event_type,
        "payload": {},
    }


@pytest.mark.parametrize(
    ("event_type", "wire_type"),
    [
        ("run_started", "legacy_run_started"),
        ("run_resumed", "legacy_run_resumed"),
    ],
)
def test_legacy_run_head_names_are_disjoint_from_canonical(
    event_type: str, wire_type: str
) -> None:
    event = run_event_notification_from_payload(
        room_id="room-1",
        payload=_legacy_payload(event_type),
        correlation_id="request-1",
    )
    assert event.run_event_type == wire_type
    assert event.payload == {}


@pytest.mark.parametrize("malformed_payload", [[], "", 0, None])
def test_falsey_malformed_run_started_payload_is_not_legacy_aliased(
    malformed_payload: object,
) -> None:
    payload = _legacy_payload("run_started")
    payload["payload"] = malformed_payload
    with pytest.raises(ValidationError):
        run_event_notification_from_payload(room_id="room-1", payload=payload)


def test_true_canonical_run_started_remains_strict() -> None:
    payload = {
        "event_id": "event-canonical",
        "run_id": "run-1",
        "seq": 0,
        "type": "run_started",
        "payload": {
            "hybro_turn_id": "run-1",
            "user_message_id": "user-1",
            "started_at": datetime(2030, 1, 1, tzinfo=UTC),
            "mode": "supervisor",
        },
        "correlation_id": "request-1",
    }
    event = run_event_notification_from_payload(room_id="room-1", payload=payload)
    assert event.run_event_type == "run_started"

    payload["payload"] = {**payload["payload"], "private": "forbidden"}
    with pytest.raises(ValidationError):
        run_event_notification_from_payload(room_id="room-1", payload=payload)

    payload["payload"] = {"hybro_turn_id": "run-1"}
    with pytest.raises(ValidationError):
        run_event_notification_from_payload(room_id="room-1", payload=payload)


@pytest.mark.asyncio
async def test_nonterminal_preflight_emits_only_processing_compatibility_status() -> (
    None
):
    publisher = SimpleNamespace(emit=AsyncMock())
    lifecycle = SimpleNamespace(
        record_processing_status=AsyncMock(return_value=_legacy_payload("run_started"))
    )
    resolver = SimpleNamespace(
        resolve_client_request_id=AsyncMock(return_value="request-1")
    )

    await emit_processing_status(
        room_id="room-1",
        status="processing",
        message_id="message-1",
        run_lifecycle=lifecycle,
        event_publisher=publisher,
        run_event_enabled=lambda: True,
        client_request_id_resolver=resolver,
        client_request_id="request-1",
    )

    publisher.emit.assert_awaited_once()
    emitted = publisher.emit.await_args.args[0]
    assert isinstance(emitted, ProcessingStatusEvent)
