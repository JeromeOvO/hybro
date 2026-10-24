from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta


def _setting(name: str, fallback):
    from common.config import settings

    return getattr(settings, name, fallback)


@dataclass(frozen=True)
class CompactionConfig:
    enabled: bool = field(default_factory=lambda: _setting("compaction_enabled", True))
    max_full_turns: int = field(
        default_factory=lambda: _setting("compaction_max_full_turns", 20)
    )
    max_total_tokens: int = field(
        default_factory=lambda: _setting("compaction_max_total_tokens", 80000)
    )
    preserve_recent_turns: int = field(
        default_factory=lambda: _setting("compaction_preserve_recent", 10)
    )
    content_ttl_days: int = field(
        default_factory=lambda: _setting("compaction_content_ttl_days", 0)
    )
    concurrency: int = field(
        default_factory=lambda: _setting("compaction_concurrency", 5)
    )

    def __post_init__(self) -> None:
        for name in (
            "max_full_turns",
            "max_total_tokens",
            "preserve_recent_turns",
            "content_ttl_days",
        ):
            _require_non_negative(name, getattr(self, name))
        if self.preserve_recent_turns > self.max_full_turns:
            raise ValueError("preserve_recent_turns must not exceed max_full_turns")
        if self.concurrency < 1:
            raise ValueError("concurrency must be at least 1")

    def expires_delta(self) -> timedelta | None:
        if self.content_ttl_days <= 0:
            return None
        return timedelta(days=self.content_ttl_days)


def _require_non_negative(name: str, value: int) -> None:
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
