"""Data shapes owned by the conversation module."""

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import Field

from common.dto.base import FrozenDTO


class SavedAgentGroupSnapshot(FrozenDTO):
    group_id: str
    name: str
    owner_id: str | None = None
    type: str | None = None
    agent_ids: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class UserMessageInsertResult:
    """Atomic user-message insert outcome returned by persistence adapters."""

    message_id: str
    created: bool
    document: dict[str, Any]


@dataclass(frozen=True)
class TimelinePosition:
    timeline_sort_us: int
    source: Literal["user", "agent"]
    message_id: str


@dataclass(frozen=True)
class RoomTimelineEntry:
    source: Literal["user", "agent"]
    message: Any


@dataclass(frozen=True)
class RoomTimelinePage:
    entries: list[RoomTimelineEntry]
    has_more: bool
    next_position: TimelinePosition | None


__all__ = [
    "RoomTimelineEntry",
    "RoomTimelinePage",
    "SavedAgentGroupSnapshot",
    "TimelinePosition",
    "UserMessageInsertResult",
]
