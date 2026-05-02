"""Unit tests for processing_message_id projector (§N.1)."""

from datetime import datetime, timezone

from services.run_projector import compute_processing_message_id_mirror


def test_mirror_empty_runs():
    assert compute_processing_message_id_mirror([]) is None


def test_mirror_single_run():
    out = compute_processing_message_id_mirror(
        [
            {
                "run_id": "a",
                "trigger_message_id": "msg-a",
                "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
            }
        ]
    )
    assert out == "msg-a"


def test_mirror_earliest_created_wins():
    r1 = {
        "run_id": "later",
        "trigger_message_id": "msg-late",
        "created_at": datetime(2025, 1, 2, tzinfo=timezone.utc),
    }
    r0 = {
        "run_id": "earlier",
        "trigger_message_id": "msg-early",
        "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
    }
    assert compute_processing_message_id_mirror([r1, r0]) == "msg-early"
    assert compute_processing_message_id_mirror([r0, r1]) == "msg-early"


def test_mirror_skips_missing_trigger():
    out = compute_processing_message_id_mirror(
        [{"run_id": "x", "trigger_message_id": None, "created_at": "2025-01-01T00:00:00+00:00"}]
    )
    assert out is None
