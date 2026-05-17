from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import timedelta


def _setting(name: str, fallback):
    from common.config import settings

    return getattr(settings, name, fallback)


def _env_int(name: str, fallback: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(fallback))))
    except (TypeError, ValueError):
        return fallback


@dataclass(frozen=True)
class TokenBudgetConfig:
    model_context_window: int = field(
        default_factory=lambda: _setting("context_model_window", 128000)
    )
    system_prompt: int = field(
        default_factory=lambda: _setting("context_system_prompt_tokens", 2000)
    )
    tool_schemas: int = field(
        default_factory=lambda: _setting("context_tool_schema_tokens", 3000)
    )
    response_reserve: int = field(
        default_factory=lambda: _setting("context_response_reserve_tokens", 4000)
    )
    room_context_pct: float = field(
        default_factory=lambda: _setting("context_room_pct", 0.15)
    )
    conversation_history_pct: float = field(
        default_factory=lambda: _setting("context_history_pct", 0.60)
    )
    current_task_pct: float = field(
        default_factory=lambda: _setting("context_task_pct", 0.25)
    )

    @property
    def fixed_reserve_tokens(self) -> int:
        return self.system_prompt + self.tool_schemas + self.response_reserve

    @property
    def available_for_content(self) -> int:
        return max(0, self.model_context_window - self.fixed_reserve_tokens)

    @property
    def room_context_tokens(self) -> int:
        return int(self.available_for_content * self.room_context_pct)

    @property
    def conversation_history_tokens(self) -> int:
        return int(self.available_for_content * self.conversation_history_pct)

    @property
    def current_task_tokens(self) -> int:
        return int(self.available_for_content * self.current_task_pct)

    def with_model_window(self, token_budget: int) -> "TokenBudgetConfig":
        model_context_window = max(0, int(token_budget))
        return TokenBudgetConfig(
            model_context_window=model_context_window,
            system_prompt=self.system_prompt,
            tool_schemas=self.tool_schemas,
            response_reserve=self.response_reserve,
            room_context_pct=self.room_context_pct,
            conversation_history_pct=self.conversation_history_pct,
            current_task_pct=self.current_task_pct,
        )


@dataclass(frozen=True)
class CompactionConfig:
    enabled: bool = field(
        default_factory=lambda: _setting("compaction_enabled", True)
    )
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
        default_factory=lambda: _env_int("COMPACTION_CONCURRENCY", 5)
    )

    def expires_delta(self) -> timedelta | None:
        if self.content_ttl_days <= 0:
            return None
        return timedelta(days=self.content_ttl_days)


@dataclass(frozen=True)
class MemorySearchConfig:
    enabled: bool = field(
        default_factory=lambda: _setting("memory_search_enabled", True)
    )
    vector_weight: float = field(
        default_factory=lambda: _setting("memory_search_vector_weight", 0.7)
    )
    keyword_weight: float = field(
        default_factory=lambda: _setting("memory_search_keyword_weight", 0.3)
    )
    temporal_decay_enabled: bool = field(
        default_factory=lambda: _setting("memory_search_temporal_decay_enabled", True)
    )
    half_life_days: int = field(
        default_factory=lambda: _setting("memory_search_half_life_days", 30)
    )
    mmr_lambda: float = field(
        default_factory=lambda: _setting("memory_search_mmr_lambda", 0.7)
    )
    max_results: int = field(
        default_factory=lambda: _setting("memory_search_max_results", 10)
    )
    max_snippet_chars: int = field(
        default_factory=lambda: _setting("memory_search_max_snippet_chars", 500)
    )
    index_name: str = field(
        default_factory=lambda: _memory_search_index_name()
    )


@dataclass(frozen=True)
class ContextMemoryLLMConfig:
    turn_notes_model: str = "context_memory_legacy_json_model"
    summary_model: str = "context_memory_legacy_json_model"


def _memory_search_index_name() -> str:
    from common.config import get_memory_search_index_name

    return get_memory_search_index_name()
