from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta, timezone

import pytest

from common.dto import TimelinePosition
from room.timeline import (
    MAX_BSON_INT64,
    MAX_CURSOR_LENGTH,
    MIN_BSON_INT64,
    TimelineCursorError,
    decode_timeline_cursor,
    encode_timeline_cursor,
    normalize_timeline_document,
    timeline_sort_us_from_value,
)


def _token(payload) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


def _valid_payload(**updates):
    payload = {
        "v": 1,
        "room_id": "room-1",
        "direction": "before",
        "timeline_sort_us": 1785700000000000,
        "source": "agent",
        "message_id": "message-123",
    }
    payload.update(updates)
    return payload


def test_cursor_round_trip():
    position = TimelinePosition(
        timeline_sort_us=1785700000000000,
        source="agent",
        message_id="message-123",
    )
    cursor = encode_timeline_cursor("room-1", position)

    assert decode_timeline_cursor(cursor, room_id="room-1") == position
    assert "room-1" not in cursor


@pytest.mark.parametrize(
    "cursor,room_id",
    [
        ("not+base64", "room-1"),
        (base64.urlsafe_b64encode(b"not-json").decode().rstrip("="), "room-1"),
        (_token("not-json-object"), "room-1"),
        (_token([1, 2]), "room-1"),
        (_token(_valid_payload(v=2)), "room-1"),
        (_token(_valid_payload(v=True)), "room-1"),
        (_token(_valid_payload()), "wrong-room"),
        (_token(_valid_payload(source="system")), "room-1"),
        (_token(_valid_payload(direction="after")), "room-1"),
        (_token(_valid_payload(message_id="")), "room-1"),
        (_token(_valid_payload(message_id=" ")), "room-1"),
        (_token(_valid_payload(timeline_sort_us=True)), "room-1"),
        (_token(_valid_payload(timeline_sort_us="1")), "room-1"),
        (_token(_valid_payload(timeline_sort_us=MAX_BSON_INT64 + 1)), "room-1"),
        (_token(_valid_payload(timeline_sort_us=MIN_BSON_INT64 - 1)), "room-1"),
        (
            _token(
                {key: value for key, value in _valid_payload().items() if key != "v"}
            ),
            "room-1",
        ),
        ("a" * (MAX_CURSOR_LENGTH + 1), "room-1"),
    ],
)
def test_cursor_strict_validation(cursor, room_id):
    with pytest.raises(TimelineCursorError, match="Invalid timeline cursor"):
        decode_timeline_cursor(cursor, room_id=room_id)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1970-01-01T00:00:00Z", 0),
        ("1970-01-01T08:00:00+08:00", 0),
    ],
)
def test_timeline_timestamp_iso_normalization(value, expected):
    assert timeline_sort_us_from_value(value) == expected


@pytest.mark.parametrize(
    "created_at",
    [
        datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=UTC),
        datetime(2026, 1, 2, 3, 4, 5, 987654),
        datetime(
            2026,
            1,
            2,
            11,
            4,
            5,
            456789,
            tzinfo=timezone(timedelta(hours=8)),
        ),
    ],
)
def test_datetime_insert_key_matches_bson_millisecond_round_trip(created_at):
    document = {"message_created_at": created_at}

    normalized = normalize_timeline_document(document)
    stored_created_at = normalized["message_created_at"]

    assert document["message_created_at"] is created_at
    assert stored_created_at.microsecond % 1000 == 0
    assert normalized["timeline_sort_us"] == timeline_sort_us_from_value(
        stored_created_at
    )


def test_iso_insert_retains_represented_microseconds():
    created_at = "2026-01-02T03:04:05.123456Z"

    normalized = normalize_timeline_document({"message_created_at": created_at})

    assert normalized["message_created_at"] == created_at
    assert normalized["timeline_sort_us"] % 1_000_000 == 123456
