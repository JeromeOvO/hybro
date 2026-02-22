from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from a2a.types import AgentCard

from common.utils.cancellation import CancellationToken
from models.room import RoomAgentMessage


class ProcessingStatus(Enum):
    """Status of message processing operations."""

    SUCCESS = "success"
    FAILED = "failed"
    CANCELED = "canceled"
    PAUSED = "paused"  # Queue paused waiting for push notification task


@dataclass
class ProcessingResult:
    """Result of single-message processing with optional metadata."""

    status: ProcessingStatus
    response_text: str = ""
    message_id: str | None = None


@dataclass
class ProcessingContext:
    """Bundles the common parameters threaded through streaming/sync sub-handlers."""

    room_id: str
    current_message: RoomAgentMessage
    agent_card: AgentCard
    user_message_id: str
    token: CancellationToken | None = None
    task_info: dict[str, Any] | None = None
    created_at: str | None = None
    step_number: int | None = None
    total_steps: int | None = None
    send_sse: bool = False

    @property
    def tracked_message_id(self) -> str | None:
        """Return message_id only if task tracking was set up."""
        return self.current_message.message_id if self.task_info else None
