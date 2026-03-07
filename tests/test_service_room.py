"""
Unit tests for RoomCenter (room_services.py) -- pure logic methods.

Tests cover:
- _looks_like_agent_id heuristic
- _normalize_room_agent_set canonical shape detection
- parse_agent_mentions extraction
- extract_agent_message_content per-agent routing
- _validate_send_message_request input validation
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from services.room_services import RoomServices


@pytest.fixture
def room_center():
    """Create a RoomCenter with mocked dependencies."""
    rc = object.__new__(RoomServices)
    rc.database_service = MagicMock()
    rc.agent_service = MagicMock()
    rc.openai_service = MagicMock()
    rc.a2a_service = MagicMock()
    rc.room_memory_service = MagicMock()
    rc.sse_manager = MagicMock()
    rc.task_service = MagicMock()
    return rc


# =============================================================================
# _looks_like_agent_id Tests
# =============================================================================


class TestLooksLikeAgentId:
    """Tests for UUID-style agent ID detection."""

    @pytest.mark.parametrize("value", [
        "550e8400-e29b-41d4-a716-446655440000",
        "550e8400e29b41d4a716446655440000",
    ])
    def test_recognizes_valid_uuids(self, value):
        assert RoomServices._looks_like_agent_id(value) is True

    @pytest.mark.parametrize("value", [
        "MyAgent",
        "agent-name",
        "",
        "not-a-uuid-at-all",
    ])
    def test_rejects_non_uuid_strings(self, value):
        assert RoomServices._looks_like_agent_id(value) is False

    def test_rejects_non_string(self):
        assert RoomServices._looks_like_agent_id(123) is False
        assert RoomServices._looks_like_agent_id(None) is False


# =============================================================================
# _normalize_room_agent_set Tests
# =============================================================================


class TestNormalizeRoomAgentSet:
    """Tests for room_agent_set normalization."""

    def test_returns_empty_for_none(self, room_center):
        assert room_center._normalize_room_agent_set(None) == {}

    def test_returns_empty_for_empty(self, room_center):
        assert room_center._normalize_room_agent_set({}) == {}

    def test_preserves_correct_shape(self, room_center):
        """Keys are UUIDs, values are names -- already canonical."""
        data = {"550e8400e29b41d4a716446655440000": "MyAgent"}
        result = room_center._normalize_room_agent_set(data)
        assert result == data

    def test_flips_inverted_shape(self, room_center):
        """Keys are names, values are UUIDs -- needs flipping."""
        data = {"MyAgent": "550e8400e29b41d4a716446655440000"}
        result = room_center._normalize_room_agent_set(data)
        assert "550e8400e29b41d4a716446655440000" in result
        assert result["550e8400e29b41d4a716446655440000"] == "MyAgent"

    def test_handles_ambiguous_data(self, room_center):
        """When keys and values both look like IDs, preserves original."""
        data = {
            "550e8400e29b41d4a716446655440000": "660e8400e29b41d4a716446655440000"
        }
        result = room_center._normalize_room_agent_set(data)
        assert result == data


# =============================================================================
# parse_agent_mentions Tests
# =============================================================================


class TestParseAgentMentions:
    """Tests for @agent mention parsing."""

    def test_parses_single_mention(self, room_center):
        text = "Hello <@agent-1|AgentOne> please help"
        agent_set = {"agent-1": "AgentOne"}
        result = room_center.parse_agent_mentions(text, agent_set)

        assert len(result) == 1
        assert result[0]["agent_id"] == "agent-1"
        assert result[0]["agent_name"] == "AgentOne"
        assert result[0]["mention_text"] == "<@agent-1|AgentOne>"

    def test_parses_multiple_mentions(self, room_center):
        text = "<@a1|Alpha> do X and <@a2|Beta> do Y"
        agent_set = {"a1": "Alpha", "a2": "Beta"}
        result = room_center.parse_agent_mentions(text, agent_set)

        assert len(result) == 2
        assert result[0]["agent_id"] == "a1"
        assert result[1]["agent_id"] == "a2"

    def test_ignores_unknown_agent(self, room_center):
        """Agent not in room should be silently ignored."""
        text = "<@unknown|Ghost> do something"
        agent_set = {}
        result = room_center.parse_agent_mentions(text, agent_set)

        assert len(result) == 0

    def test_returns_empty_for_no_mentions(self, room_center):
        text = "Just a normal message with no mentions"
        result = room_center.parse_agent_mentions(text, {"a1": "Alpha"})
        assert result == []

    def test_preserves_position_order(self, room_center):
        text = "<@b|Beta> then <@a|Alpha>"
        agent_set = {"a": "Alpha", "b": "Beta"}
        result = room_center.parse_agent_mentions(text, agent_set)

        assert result[0]["agent_id"] == "b"
        assert result[1]["agent_id"] == "a"


# =============================================================================
# extract_agent_message_content Tests
# =============================================================================


class TestExtractAgentMessageContent:
    """Tests for per-agent message content extraction."""

    def test_extracts_content_for_mentioned_agent(self, room_center):
        text = "<@a1|Alpha> write code. <@a2|Beta> review it."
        mentions = [
            {"agent_id": "a1", "agent_name": "Alpha", "mention_text": "<@a1|Alpha>", "position": 0},
            {"agent_id": "a2", "agent_name": "Beta", "mention_text": "<@a2|Beta>", "position": 22},
        ]

        result = room_center.extract_agent_message_content(text, "a1", "Alpha", mentions)
        assert "write code" in result
        assert "<@" not in result

    def test_returns_clean_text_when_agent_not_mentioned(self, room_center):
        """Agent not in mentions gets full text with all mentions stripped."""
        text = "<@a1|Alpha> do something"
        mentions = [
            {"agent_id": "a1", "agent_name": "Alpha", "mention_text": "<@a1|Alpha>", "position": 0},
        ]

        result = room_center.extract_agent_message_content(text, "a2", "Beta", mentions)
        assert "<@" not in result
        assert "do something" in result


# =============================================================================
# _validate_send_message_request Tests
# =============================================================================


class TestValidateSendMessageRequest:
    """Tests for send_message input validation."""

    def test_returns_none_for_valid_request(self, room_center):
        req = MagicMock()
        req.room_id = "room-001"
        req.message = MagicMock()
        assert room_center._validate_send_message_request(req) is None

    def test_returns_error_when_room_id_missing(self, room_center):
        req = MagicMock()
        req.room_id = None
        req.message = MagicMock()
        result = room_center._validate_send_message_request(req)
        assert result is not None
        assert result.success is False
        assert result.status_code == 400

    def test_returns_error_when_message_missing(self, room_center):
        req = MagicMock()
        req.room_id = "room-001"
        req.message = None
        result = room_center._validate_send_message_request(req)
        assert result is not None
        assert result.success is False
        assert result.status_code == 400
