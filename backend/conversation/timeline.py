from __future__ import annotations

import base64
import binascii
import json
from datetime import UTC, datetime
from typing import Any

from common.dto.room import TimelinePosition

CURSOR_VERSION = 1
MAX_CURSOR_LENGTH = 1024
MIN_BSON_INT64 = -(2**63)
MAX_BSON_INT64 = 2**63 - 1
SOURCE_RANK = {"user": 0, "agent": 1}
TIMELINE_MIGRATION_VERSION = 1
TIMELINE_MIGRATION_MARKER_COLLECTION = "migration_markers"
TIMELINE_MIGRATION_MARKER_ID = "room_timeline_sort_keys_v1"
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class TimelineCursorError(ValueError):
    """Raised when an opaque room-timeline cursor is invalid."""


def timeline_sort_us_from_value(value: Any) -> int:
    """Return deterministic UTC Unix epoch microseconds for a stored timestamp."""

    if isinstance(value, datetime):
        parsed = (
            value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        )
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("message_created_at is missing or invalid")
        if text.endswith(("Z", "z")):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError("message_created_at is missing or invalid") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        else:
            parsed = parsed.astimezone(UTC)
    else:
        raise ValueError("message_created_at is missing or invalid")

    delta = parsed - _EPOCH
    return ((delta.days * 86400 + delta.seconds) * 1_000_000) + delta.microseconds


def normalize_timeline_document(document: dict[str, Any]) -> dict[str, Any]:
    """Copy an insert document and add its private immutable timeline key."""

    candidate = dict(document)
    created_at = candidate.get("message_created_at")
    if isinstance(created_at, datetime):
        # BSON Date stores milliseconds. Derive the key from exactly the value that
        # survives a Mongo round trip instead of retaining Python-only precision.
        created_at = created_at.replace(
            microsecond=(created_at.microsecond // 1000) * 1000
        )
        candidate["message_created_at"] = created_at
    computed = timeline_sort_us_from_value(created_at)
    existing = candidate.get("timeline_sort_us")
    if existing is not None and (
        isinstance(existing, bool)
        or not isinstance(existing, int)
        or existing != computed
    ):
        raise ValueError("timeline_sort_us conflicts with message_created_at")
    candidate["timeline_sort_us"] = computed
    return candidate


def timeline_key(
    *, timeline_sort_us: int, source: str, message_id: str
) -> tuple[int, int, str]:
    try:
        rank = SOURCE_RANK[source]
    except KeyError as exc:
        raise ValueError("invalid timeline source") from exc
    return timeline_sort_us, rank, message_id


def encode_timeline_cursor(room_id: str, position: TimelinePosition) -> str:
    payload = {
        "v": CURSOR_VERSION,
        "room_id": room_id,
        "direction": "before",
        "timeline_sort_us": position.timeline_sort_us,
        "source": position.source,
        "message_id": position.message_id,
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).decode("ascii")
    return encoded.rstrip("=")


def decode_timeline_cursor(cursor: Any, *, room_id: str) -> TimelinePosition:
    if not isinstance(cursor, str) or not cursor or len(cursor) > MAX_CURSOR_LENGTH:
        raise TimelineCursorError("Invalid timeline cursor")
    try:
        if any(
            char
            not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
            for char in cursor
        ):
            raise TimelineCursorError("Invalid timeline cursor")
        padded = cursor + ("=" * (-len(cursor) % 4))
        decoded = base64.b64decode(padded, altchars=b"-_", validate=True)
        payload = json.loads(decoded.decode("utf-8"))
    except (
        binascii.Error,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
    ) as exc:
        raise TimelineCursorError("Invalid timeline cursor") from exc

    expected_fields = {
        "v",
        "room_id",
        "direction",
        "timeline_sort_us",
        "source",
        "message_id",
    }
    if not isinstance(payload, dict) or set(payload) != expected_fields:
        raise TimelineCursorError("Invalid timeline cursor")
    sort_us = payload.get("timeline_sort_us")
    message_id = payload.get("message_id")
    version = payload.get("v")
    if (
        isinstance(version, bool)
        or version != CURSOR_VERSION
        or payload.get("direction") != "before"
        or payload.get("room_id") != room_id
        or payload.get("source") not in SOURCE_RANK
        or isinstance(sort_us, bool)
        or not isinstance(sort_us, int)
        or not MIN_BSON_INT64 <= sort_us <= MAX_BSON_INT64
        or not isinstance(message_id, str)
        or not message_id.strip()
    ):
        raise TimelineCursorError("Invalid timeline cursor")
    return TimelinePosition(
        timeline_sort_us=sort_us,
        source=payload["source"],
        message_id=message_id,
    )
