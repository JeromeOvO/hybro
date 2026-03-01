"""
Unit tests for ResponseProcessor module.

Tests cover:
- _parse_sync_fallback_response: None input, message kind, task kind,
  JSONRPCErrorResponse, and default fallback
"""

import pytest
from unittest.mock import MagicMock

from a2a.types import TaskState

from modules.ResponseProcessor import ResponseProcessor


# =============================================================================
# _parse_sync_fallback_response Tests
# =============================================================================


class TestParseSyncFallbackResponse:
    """Tests for sync response parsing into normalized dict."""

    def test_returns_empty_for_none(self):
        result = ResponseProcessor._parse_sync_fallback_response(None, "msg-1")
        assert result == {"type": "message", "message_id": "msg-1", "content": ""}

    def test_parses_message_kind(self):
        part = MagicMock()
        part.text = "Hello"
        del part.root

        inner_result = MagicMock()
        inner_result.kind = "message"
        inner_result.parts = [part]

        root = MagicMock()
        root.result = inner_result

        response = MagicMock()
        response.root = root

        from a2a.types import JSONRPCErrorResponse
        assert not isinstance(response.root, JSONRPCErrorResponse)

        result = ResponseProcessor._parse_sync_fallback_response(response, "msg-1")
        assert result["type"] == "message"
        assert result["content"] == "Hello"

    def test_parses_task_kind(self):
        inner_result = MagicMock()
        inner_result.kind = "task"
        inner_result.id = "task-001"
        inner_result.status = MagicMock()
        inner_result.status.state = TaskState.completed

        root = MagicMock()
        root.result = inner_result

        response = MagicMock()
        response.root = root

        from a2a.types import JSONRPCErrorResponse
        assert not isinstance(response.root, JSONRPCErrorResponse)

        result = ResponseProcessor._parse_sync_fallback_response(response, "msg-1")
        assert result["type"] == "task"
        assert result["task_id"] == "task-001"
        assert result["status"] == "completed"

    def test_raises_on_jsonrpc_error(self):
        from a2a.types import JSONRPCErrorResponse, JSONRPCError

        error_response = JSONRPCErrorResponse(
            id="req-1",
            error=JSONRPCError(code=-32000, message="Agent offline"),
        )

        response = MagicMock()
        response.root = error_response

        from services.a2a_service import A2AServiceError

        with pytest.raises(A2AServiceError):
            ResponseProcessor._parse_sync_fallback_response(response, "msg-1")

    def test_unknown_kind_returns_empty(self):
        inner_result = MagicMock()
        inner_result.kind = "unknown"

        root = MagicMock()
        root.result = inner_result

        response = MagicMock()
        response.root = root

        from a2a.types import JSONRPCErrorResponse
        assert not isinstance(response.root, JSONRPCErrorResponse)

        result = ResponseProcessor._parse_sync_fallback_response(response, "msg-1")
        assert result == {"type": "message", "message_id": "msg-1", "content": ""}

    def test_concatenates_multiple_text_parts(self):
        p1 = MagicMock()
        p1.text = "Hello "
        del p1.root
        p2 = MagicMock()
        p2.text = "world"
        del p2.root

        inner_result = MagicMock()
        inner_result.kind = "message"
        inner_result.parts = [p1, p2]

        root = MagicMock()
        root.result = inner_result

        response = MagicMock()
        response.root = root

        from a2a.types import JSONRPCErrorResponse
        assert not isinstance(response.root, JSONRPCErrorResponse)

        result = ResponseProcessor._parse_sync_fallback_response(response, "msg-1")
        assert result["content"] == "Hello world"
