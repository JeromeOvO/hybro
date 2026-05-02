"""Tests for mongodb.py read-path sanitization helpers.

Covers _sanitize_parts, _sanitize_task_dict, and _parse_room_agent_message —
the defensive layer that strips malformed A2A Part dicts before Pydantic
validation so a single bad part doesn't poison the entire RoomAgentMessage.
"""

import pytest

from database.mongodb import _parse_room_agent_message, _sanitize_parts, _sanitize_task_dict


# ---------------------------------------------------------------------------
# _sanitize_parts
# ---------------------------------------------------------------------------
class TestSanitizeParts:
    """Unit tests for _sanitize_parts."""

    def test_valid_text_part_kept(self):
        parts = [{"kind": "text", "text": "hello"}]
        assert _sanitize_parts(parts) == parts

    def test_valid_file_part_kept(self):
        parts = [{"kind": "file", "file": {"uri": "https://example.com/f.png"}}]
        assert _sanitize_parts(parts) == parts

    def test_valid_data_part_kept(self):
        parts = [{"kind": "data", "data": {"key": "value"}}]
        assert _sanitize_parts(parts) == parts

    def test_malformed_text_part_stripped(self):
        """TextPart with kind='text' but no 'text' field is dropped."""
        parts = [{"kind": "text"}]
        assert _sanitize_parts(parts) == []

    def test_malformed_text_null_stripped(self):
        parts = [{"kind": "text", "text": None}]
        assert _sanitize_parts(parts) == []

    def test_malformed_file_part_stripped(self):
        parts = [{"kind": "file"}]
        assert _sanitize_parts(parts) == []

    def test_malformed_data_part_stripped(self):
        parts = [{"kind": "data"}]
        assert _sanitize_parts(parts) == []

    def test_mixed_valid_and_malformed(self):
        """Good parts survive; bad parts are removed."""
        parts = [
            {"kind": "text", "text": "ok"},
            {"kind": "text"},
            {"kind": "file", "file": {"uri": "s3://bucket/key"}},
            {"kind": "data"},
        ]
        result = _sanitize_parts(parts)
        assert len(result) == 2
        assert result[0] == {"kind": "text", "text": "ok"}
        assert result[1] == {"kind": "file", "file": {"uri": "s3://bucket/key"}}

    def test_empty_text_is_valid(self):
        """A TextPart with text='' is valid (the field exists, even if empty)."""
        parts = [{"kind": "text", "text": ""}]
        assert _sanitize_parts(parts) == parts

    def test_empty_list_returns_empty(self):
        assert _sanitize_parts([]) == []

    def test_wrapped_in_root(self):
        """Parts stored with a 'root' wrapper are handled."""
        parts = [{"root": {"kind": "text"}}]
        assert _sanitize_parts(parts) == []

    def test_wrapped_valid_text_in_root(self):
        parts = [{"root": {"kind": "text", "text": "hi"}}]
        assert _sanitize_parts(parts) == parts

    def test_unknown_kind_without_content_key_dropped(self):
        """Parts with unknown kind and no recognized content key are dropped."""
        parts = [{"kind": "unknown", "payload": "x"}]
        assert _sanitize_parts(parts) == []

    def test_unknown_kind_with_content_key_kept(self):
        """Parts with unknown kind but a recognized content key are kept."""
        parts = [{"kind": "custom", "text": "hello"}]
        assert _sanitize_parts(parts) == parts

    def test_no_kind_with_content_key_kept(self):
        """Parts without 'kind' but with a recognized content key are kept."""
        parts = [{"url": "https://example.com/doc.pdf"}]
        assert _sanitize_parts(parts) == parts

    def test_no_kind_without_content_key_dropped(self):
        """Parts without 'kind' and no recognized content key are dropped."""
        parts = [{"something": "else"}]
        assert _sanitize_parts(parts) == []


# ---------------------------------------------------------------------------
# _sanitize_task_dict
# ---------------------------------------------------------------------------
class TestSanitizeTaskDict:
    """Unit tests for _sanitize_task_dict."""

    def test_sanitizes_artifact_parts(self):
        task = {
            "artifacts": [
                {"artifactId": "a1", "parts": [{"kind": "text"}, {"kind": "text", "text": "ok"}]}
            ]
        }
        result = _sanitize_task_dict(task)
        assert len(result["artifacts"][0]["parts"]) == 1
        assert result["artifacts"][0]["parts"][0]["text"] == "ok"

    def test_sanitizes_history_parts(self):
        task = {
            "history": [
                {"role": "agent", "parts": [{"kind": "data"}]}
            ]
        }
        result = _sanitize_task_dict(task)
        assert result["history"][0]["parts"] == []

    def test_sanitizes_status_message_parts(self):
        task = {
            "status": {
                "message": {
                    "parts": [{"kind": "file"}]
                }
            }
        }
        result = _sanitize_task_dict(task)
        assert task["status"]["message"]["parts"] == []

    def test_no_artifacts_or_history(self):
        """No crash on empty/missing fields."""
        task = {}
        result = _sanitize_task_dict(task)
        assert result == {}

    def test_none_parts_ignored(self):
        """If parts is None, it's left alone."""
        task = {"artifacts": [{"artifactId": "a1", "parts": None}]}
        result = _sanitize_task_dict(task)
        assert result["artifacts"][0]["parts"] is None

    def test_multiple_artifacts(self):
        task = {
            "artifacts": [
                {"artifactId": "a1", "parts": [{"kind": "text"}]},
                {"artifactId": "a2", "parts": [{"kind": "text", "text": "keep"}]},
            ]
        }
        _sanitize_task_dict(task)
        assert len(task["artifacts"][0]["parts"]) == 0
        assert len(task["artifacts"][1]["parts"]) == 1


# ---------------------------------------------------------------------------
# _parse_room_agent_message
# ---------------------------------------------------------------------------
class TestParseRoomAgentMessage:
    """Integration tests for _parse_room_agent_message."""

    @staticmethod
    def _make_raw(task_dict: dict | None = None, text: str | None = None) -> dict:
        """Build a minimal valid RoomAgentMessage raw dict."""
        mc: dict = {}
        if task_dict is not None:
            mc["message_task"] = task_dict
        if text is not None:
            mc["message_text"] = text
        return {
            "room_id": "room-1",
            "message_id": "msg-1",
            "message_type": "agent",
            "agent_id": "agent-1",
            "message_content": mc,
        }

    def test_clean_doc_passes(self):
        raw = self._make_raw(text="hello")
        msg = _parse_room_agent_message(raw)
        assert msg.message_content.message_text == "hello"

    def test_malformed_part_stripped_before_validation(self):
        """The exact scenario that caused the original crash."""
        task_dict = {
            "id": "task-1",
            "contextId": "ctx-1",
            "status": {"state": "completed"},
            "artifacts": [
                {
                    "artifactId": "art-1",
                    "parts": [
                        {"kind": "text"},
                        {"kind": "text", "text": "real content"},
                    ],
                }
            ],
        }
        msg = _parse_room_agent_message(self._make_raw(task_dict=task_dict))
        arts = msg.message_content.message_task.artifacts
        assert len(arts) == 1
        assert len(arts[0].parts) == 1
        assert arts[0].parts[0].root.text == "real content"

    def test_no_task_no_crash(self):
        raw = self._make_raw(text="just text")
        msg = _parse_room_agent_message(raw)
        assert msg.message_content.message_task is None

    def test_all_parts_malformed_yields_empty_artifact(self):
        task_dict = {
            "id": "task-2",
            "contextId": "ctx-2",
            "status": {"state": "working"},
            "artifacts": [
                {
                    "artifactId": "art-1",
                    "parts": [{"kind": "text"}, {"kind": "file"}],
                }
            ],
        }
        msg = _parse_room_agent_message(self._make_raw(task_dict=task_dict))
        assert len(msg.message_content.message_task.artifacts[0].parts) == 0
