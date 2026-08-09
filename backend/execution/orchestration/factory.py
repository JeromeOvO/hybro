from __future__ import annotations

from typing import Any

import execution.orchestration.room_message_center as _defaults
from execution.orchestration.room_message_center import (
    BoundRoomMessageCenterProxy,
    RoomMessageCenter,
    room_message_center,
)


def create_room_message_center(**kwargs: Any) -> RoomMessageCenter:
    guardrails_enabled = kwargs.pop("guardrails_enabled", None)
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
        "internal_event_publisher": _defaults.internal_event_publisher,
        "coordinator": _defaults.coordinator,
        "summary_service": _defaults.summary_service,
        "task_notifier": _defaults.task_notifier,
        "task_notification_store": default_store,
        "agent_resolver_service": _defaults.agent_resolver_service,
        "a2a_transport": _defaults.a2a_transport,
        "remote_task_reader": _defaults.remote_task_reader,
        "room_memory": _defaults.room_memory,
        "rate_limit_service": _defaults.rate_limit_service,
        "room_supervisor_service": _defaults.room_supervisor_service,
        "orchestration_run_store": _defaults.orchestration_run_store,
        "orchestration_planner": _defaults.orchestration_planner,
        "orchestration_resource_provider": None,
        "hitl_coordinator": None,
        "task_notifications": None,
        "task_notification_impl": None,
        "agent_health_service": None,
        "room_files": None,
        "capability_issue_service": None,
        "context_assembly": _defaults.context_assembly,
        "memory_search": _defaults.memory_search,
        "context_compaction": _defaults.context_compaction,
        "build_turn_content_func": _defaults.build_turn_content,
        "supervisor_planning_error_cls": _defaults.SupervisorPlanningError,
        "orphan_threshold_minutes": _defaults.settings.orphan_threshold_minutes,
        "cloud_health_cache_ttl": 30.0,
        "cloud_health_check_timeout": 5.0,
    }
    deps.update(kwargs)
    if deps.get("cancellation_control") is None:
        raise RuntimeError(
            "RoomMessageCenter cancellation_control dependency is required"
        )
    if deps.get("internal_event_publisher") is None:
        raise RuntimeError(
            "RoomMessageCenter internal_event_publisher dependency is required"
        )
    room_message_center = RoomMessageCenter(**deps)
    if guardrails_enabled is not None:
        room_message_center.supervisor_executor.guardrails_enabled = bool(
            guardrails_enabled
        )
    return room_message_center


__all__ = [
    "BoundRoomMessageCenterProxy",
    "create_room_message_center",
    "room_message_center",
]
