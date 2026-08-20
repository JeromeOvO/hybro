"""Pure tool contract checks."""

from __future__ import annotations

from .models import ToolCall, ToolResult


class ToolCorrelationError(ValueError):
    """Raised when an observation does not belong to its requested tool call."""


def validate_tool_result_correlation(call: ToolCall, result: ToolResult) -> None:
    """Require stable call and tool identity across execution."""

    if result.call_id != call.call_id or result.tool_name != call.tool_name:
        raise ToolCorrelationError("ToolResult does not correlate to ToolCall")
