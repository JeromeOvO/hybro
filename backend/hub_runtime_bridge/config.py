from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class HubRuntimeBridgeConfig:
    heartbeat_interval_seconds: int = 30
    heartbeat_miss_limit: int = 3
    heartbeat_ttl_seconds: int = 90
    offline_queue_max: int = 1000
    offline_queue_ttl_seconds: int = 3600
    offline_grace_period_seconds: int = 300
    stream_maxlen: int = 10_000
    ownership_lease_ttl_seconds: int = 120
    journal_claim_ttl_seconds: int = 120
    replay_batch_size: int = 100
    replay_interval_seconds: int = 5

    def __post_init__(self) -> None:
        for name, value in self.__dict__.items():
            if isinstance(value, int) and value <= 0:
                raise ValueError(f"{name} must be positive")


def config_from_settings(settings_obj: Any) -> HubRuntimeBridgeConfig:
    return HubRuntimeBridgeConfig(
        heartbeat_interval_seconds=int(
            getattr(settings_obj, "relay_heartbeat_interval", 30)
        ),
        heartbeat_miss_limit=int(
            getattr(settings_obj, "relay_hub_agent_heartbeat_miss_limit", 3)
        ),
        heartbeat_ttl_seconds=int(getattr(settings_obj, "relay_hub_heartbeat_ttl", 90)),
        offline_queue_max=int(getattr(settings_obj, "relay_offline_queue_max", 1000)),
        offline_queue_ttl_seconds=int(
            getattr(settings_obj, "relay_offline_queue_ttl", 3600)
        ),
        offline_grace_period_seconds=int(
            getattr(settings_obj, "relay_offline_grace_period", 300)
        ),
        stream_maxlen=int(getattr(settings_obj, "relay_stream_maxlen", 10_000)),
    )


__all__ = ["HubRuntimeBridgeConfig", "config_from_settings"]
