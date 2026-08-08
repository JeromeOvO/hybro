from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

DEFAULT_TERMINAL_PROCESSING_STATUSES = frozenset(
    {"completed", "failed", "canceled", "rejected", "rate_limited", "error"}
)


@dataclass(frozen=True)
class DeliveryConfig:
    heartbeat_interval_seconds: float = 30.0
    sse_connection_queue_maxsize: int = 100
    shutdown_drain_seconds: float = 5.0
    terminal_dedup_ttl_seconds: int = 300
    terminal_dedup_cache_maxsize: int = 10_000
    delivery_started_ttl_seconds: int = 3600
    delivery_started_cache_maxsize: int = 10_000
    redis_sse_channel_prefix: str = "sse:room:"
    redis_dead_letter_channel: str = "delivery:dead_letter"
    redis_terminal_key_prefix: str = "terminal:"
    dead_letter_memory_maxlen: int = 1000
    redis_reconnect_delay: float = 1.0
    redis_reconnect_max_delay: float = 30.0
    redis_max_connections: int = 50
    redis_subscription_reserved_connections: int = 10
    redis_room_subscription_production_limit: int = 40
    redis_room_subscription_ready_timeout_seconds: float = 5.0
    terminal_processing_statuses: frozenset[str] | Iterable[str] = field(
        default_factory=lambda: DEFAULT_TERMINAL_PROCESSING_STATUSES
    )

    def __post_init__(self) -> None:
        _require_positive("heartbeat_interval_seconds", self.heartbeat_interval_seconds)
        _require_positive(
            "sse_connection_queue_maxsize", self.sse_connection_queue_maxsize
        )
        _require_positive("shutdown_drain_seconds", self.shutdown_drain_seconds)
        _require_positive("terminal_dedup_ttl_seconds", self.terminal_dedup_ttl_seconds)
        _require_positive(
            "terminal_dedup_cache_maxsize",
            self.terminal_dedup_cache_maxsize,
        )
        _require_positive(
            "delivery_started_ttl_seconds", self.delivery_started_ttl_seconds
        )
        _require_positive(
            "delivery_started_cache_maxsize", self.delivery_started_cache_maxsize
        )
        _require_positive("dead_letter_memory_maxlen", self.dead_letter_memory_maxlen)
        _require_positive("redis_reconnect_delay", self.redis_reconnect_delay)
        _require_positive("redis_reconnect_max_delay", self.redis_reconnect_max_delay)
        _require_positive("redis_max_connections", self.redis_max_connections)
        _require_positive(
            "redis_subscription_reserved_connections",
            self.redis_subscription_reserved_connections,
        )
        _require_positive(
            "redis_room_subscription_production_limit",
            self.redis_room_subscription_production_limit,
        )
        _require_positive(
            "redis_room_subscription_ready_timeout_seconds",
            self.redis_room_subscription_ready_timeout_seconds,
        )
        if self.redis_reconnect_max_delay < self.redis_reconnect_delay:
            raise ValueError(
                "redis_reconnect_max_delay must be greater than or equal to redis_reconnect_delay"
            )
        if (
            self.redis_room_subscription_production_limit
            + self.redis_subscription_reserved_connections
            > self.redis_max_connections
        ):
            raise ValueError(
                "redis_room_subscription_production_limit plus reserved connections "
                "must not exceed redis_max_connections"
            )

        for field_name in (
            "redis_sse_channel_prefix",
            "redis_dead_letter_channel",
            "redis_terminal_key_prefix",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{field_name} must be a non-empty string")

        object.__setattr__(
            self,
            "terminal_processing_statuses",
            _normalize_terminal_statuses(self.terminal_processing_statuses),
        )


def _require_positive(field_name: str, value: float | int) -> None:
    _require_at_least(field_name, value, 0, strict=True)


def _require_at_least(
    field_name: str,
    value: float | int,
    minimum: float | int,
    *,
    strict: bool = False,
) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{field_name} must be numeric")
    invalid = value <= minimum if strict else value < minimum
    if invalid:
        qualifier = "greater than" if strict else "greater than or equal to"
        raise ValueError(f"{field_name} must be {qualifier} {minimum}")


def _normalize_terminal_statuses(
    value: frozenset[str] | Iterable[str],
) -> frozenset[str]:
    if isinstance(value, str):
        raise ValueError("terminal_processing_statuses must be an iterable of strings")
    statuses = []
    for status in value:
        if not isinstance(status, str):
            raise ValueError("terminal_processing_statuses must contain only strings")
        normalized = status.strip().lower()
        if not normalized:
            raise ValueError("terminal_processing_statuses must not contain blanks")
        statuses.append(normalized)
    if not statuses:
        raise ValueError("terminal_processing_statuses must not be empty")
    return frozenset(statuses)


__all__ = ["DeliveryConfig"]
