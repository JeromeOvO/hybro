from __future__ import annotations

from typing import Any

from approval.service import HITLService


def create_hitl_service(**kwargs: Any) -> HITLService:
    allowed = {
        "persistence",
        "delivery",
        "terminal_lifecycle",
        "lifecycle",
        "application",
        "room_files",
        "canonical_control_publisher",
        "canonical_run_state_reader",
        "supervisor_resume",
        "canonical_cancellation_requester",
        "public_secret_values",
        "approval_deadline_hours",
        "public_text_sanitizer",
    }
    for name in kwargs:
        if name not in allowed:
            raise TypeError(f"create_hitl_service got unexpected argument {name!r}")

    service = HITLService(
        terminal_lifecycle=kwargs.get("terminal_lifecycle"),
        lifecycle=kwargs.get("lifecycle"),
        application=kwargs.get("application"),
        room_files=kwargs.get("room_files"),
        canonical_control_publisher=kwargs.get("canonical_control_publisher"),
        canonical_run_state_reader=kwargs.get("canonical_run_state_reader"),
        supervisor_resume=kwargs.get("supervisor_resume"),
        canonical_cancellation_requester=kwargs.get("canonical_cancellation_requester"),
        public_secret_values=kwargs.get("public_secret_values", ()),
        approval_deadline_hours=kwargs.get("approval_deadline_hours"),
        public_text_sanitizer=kwargs.get("public_text_sanitizer"),
    )
    service._persistence = kwargs.get("persistence")
    service._delivery = kwargs.get("delivery")
    return service


__all__ = ["create_hitl_service"]
