from __future__ import annotations

from typing import Any

import execution.orchestration.room_message_center as _defaults
from execution.orchestration.room_message_center import (
    BoundRoomMessageCenterProxy,
    RoomMessageCenter,
    room_message_center,
)


def create_room_message_center(
    *,
    debate_rounds: int,
    **kwargs: Any,
) -> RoomMessageCenter:
    default_store = _defaults.default_store
    deps: dict[str, Any] = {
        "room_runtime": _defaults.room_runtime,
        "message_reader": default_store,
        "message_writer": default_store,
        "task_state_store": default_store,
        "continuation_store": default_store,
        "agent_lookup": default_store,
        "agent_group_reader": default_store,
        "room_reader": default_store,
        "room_writer": default_store,
        "memory_reader": default_store,
        "memory_writer": default_store,
        "hitl_reader": default_store,
        "delivery": _defaults.delivery,
        "event_publisher": _defaults.event_publisher,
        "coordinator": _defaults.coordinator,
        "summary_service": _defaults.summary_service,
        "notification_service": _defaults.notification_service,
        "agent_resolver_service": _defaults.agent_resolver_service,
        "a2a_transport": _defaults.a2a_transport,
        "remote_task_reader": _defaults.remote_task_reader,
        "room_memory": _defaults.room_memory,
        "debate_service": _defaults.debate_service,
        "rate_limit_service": _defaults.rate_limit_service,
        "room_supervisor_service": _defaults.room_supervisor_service,
        "hitl_coordinator": None,
        "task_notifications": None,
        "task_notification_impl": None,
        "agent_health_service": None,
        "object_storage": None,
        "capability_issue_service": None,
        "context_memory_runtime": _defaults.context_memory_runtime,
        "context_compaction": _defaults.context_compaction,
        "build_turn_content_func": _defaults.build_turn_content,
        "supervisor_planning_error_cls": _defaults.SupervisorPlanningError,
        "orphan_threshold_minutes": _defaults.settings.orphan_threshold_minutes,
        "debate_rounds": debate_rounds,
        "cloud_health_cache_ttl": 30.0,
        "cloud_health_check_timeout": 5.0,
    }
    if "s3_service" in kwargs and "object_storage" not in kwargs:
        kwargs["object_storage"] = kwargs.pop("s3_service")
    deps.update(kwargs)
    if deps.get("event_publisher") is None:
        raise RuntimeError("RoomMessageCenter event_publisher dependency is required")
    return RoomMessageCenter(**deps)


__all__ = [
    "BoundRoomMessageCenterProxy",
    "create_room_message_center",
    "room_message_center",
]
