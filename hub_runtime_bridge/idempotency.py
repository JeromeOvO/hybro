from __future__ import annotations

import hashlib
import json
from uuid import uuid4


def stable_response_key(hub_id: str, task_id: str | None, response_seq: object) -> str | None:
    if not hub_id or not task_id or response_seq is None:
        return None
    return f"hub-response:{hub_id}:{task_id}:{response_seq}"


def legacy_correlation_fingerprint(
    hub_id: str, room_id: str, agent_message_id: str, event_type: str, batch_index: int, payload: dict
) -> str:
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return f"legacy:{hub_id}:{room_id}:{agent_message_id}:{event_type}:{batch_index}:{digest}"


def ingest_idempotency_key() -> str:
    return f"ingest:{uuid4()}"


__all__ = [
    "ingest_idempotency_key",
    "legacy_correlation_fingerprint",
    "stable_response_key",
]
