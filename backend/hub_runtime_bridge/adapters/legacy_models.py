from __future__ import annotations

from typing import Any

from models.hub import HubStatus, RelayToHubEvent


def relay_event_from_dict(payload: dict[str, Any]) -> RelayToHubEvent:
    return RelayToHubEvent(**payload)


def hub_status_from_info(info: Any) -> HubStatus:
    return HubStatus(
        hub_id=info.hub_id,
        is_online=info.is_online,
        agent_count=info.agent_count,
        active_agent_count=getattr(info, "active_agent_count", 0),
        inactive_agent_count=getattr(info, "inactive_agent_count", 0),
    )


__all__ = ["hub_status_from_info", "relay_event_from_dict"]
