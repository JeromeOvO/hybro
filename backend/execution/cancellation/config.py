from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CancellationStartupPolicy:
    redis_expected: bool
    multi_worker: bool
    allow_degraded_change_stream: bool = False

    def __post_init__(self) -> None:
        if self.allow_degraded_change_stream and (
            self.redis_expected or self.multi_worker
        ):
            raise ValueError(
                "degraded change stream mode is only valid without Redis and "
                "without multi-worker mode"
            )


@dataclass(frozen=True)
class CancellationConfig:
    ttl_seconds: int = 3600
    cache_maxsize: int = 10_000
    redis_channel: str = "cancel:global"
    redis_key_prefix: str = "cancelled:"
    redis_reconnect_delay: float = 1.0
    redis_reconnect_max_delay: float = 30.0
    redis_subscription_ready_timeout_seconds: float = 5.0
    redis_io_timeout_seconds: float = 5.0
    change_stream_backoff_base: float = 1.0
    change_stream_backoff_max: float = 30.0
    change_stream_backoff_factor: float = 2.0
    change_stream_jitter_fraction: float = 0.25

    def __post_init__(self) -> None:
        positive = {
            "ttl_seconds": self.ttl_seconds,
            "cache_maxsize": self.cache_maxsize,
            "redis_reconnect_delay": self.redis_reconnect_delay,
            "redis_reconnect_max_delay": self.redis_reconnect_max_delay,
            "redis_subscription_ready_timeout_seconds": (
                self.redis_subscription_ready_timeout_seconds
            ),
            "redis_io_timeout_seconds": self.redis_io_timeout_seconds,
            "change_stream_backoff_base": self.change_stream_backoff_base,
            "change_stream_backoff_max": self.change_stream_backoff_max,
        }
        for name, value in positive.items():
            if value <= 0:
                raise ValueError(f"{name} must be greater than 0")
        if self.change_stream_backoff_factor < 1:
            raise ValueError("change_stream_backoff_factor must be at least 1")
        if not 0 <= self.change_stream_jitter_fraction <= 1:
            raise ValueError("change_stream_jitter_fraction must be between 0 and 1")
        if self.redis_reconnect_max_delay < self.redis_reconnect_delay:
            raise ValueError(
                "redis_reconnect_max_delay must be greater than or equal to "
                "redis_reconnect_delay"
            )
        if self.change_stream_backoff_max < self.change_stream_backoff_base:
            raise ValueError(
                "change_stream_backoff_max must be greater than or equal to "
                "change_stream_backoff_base"
            )


__all__ = ["CancellationConfig", "CancellationStartupPolicy"]
