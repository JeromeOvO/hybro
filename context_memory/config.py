from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import os

from common.config import settings


@dataclass(frozen=True)
class TokenBudgetConfig:
    model_context_window: int = settings.context_model_window
    system_prompt: int = settings.context_system_prompt_tokens
    tool_schemas: int = settings.context_tool_schema_tokens
    response_reserve: int = settings.context_response_reserve_tokens
    room_context_pct: float = settings.context_room_pct
    conversation_history_pct: float = settings.context_history_pct
    current_task_pct: float = settings.context_task_pct

    @property
    def fixed_reserve_tokens(self) -> int:
        return self.system_prompt + self.tool_schemas + self.response_reserve

    @property
    def available_for_content(self) -> int:
        return self.model_context_window - self.fixed_reserve_tokens

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
        return TokenBudgetConfig(
            model_context_window=token_budget,
            system_prompt=self.system_prompt,
            tool_schemas=self.tool_schemas,
            response_reserve=self.response_reserve,
            room_context_pct=self.room_context_pct,
            conversation_history_pct=self.conversation_history_pct,
            current_task_pct=self.current_task_pct,
        )


def _compaction_concurrency_default() -> int:
    try:
        return max(1, int(os.getenv("COMPACTION_CONCURRENCY", "5")))
    except (TypeError, ValueError):
        return 5


@dataclass(frozen=True)
class CompactionConfig:
    enabled: bool = settings.compaction_enabled
    max_full_turns: int = settings.compaction_max_full_turns
    max_total_tokens: int = settings.compaction_max_total_tokens
    preserve_recent_turns: int = settings.compaction_preserve_recent
    content_ttl_days: int = settings.compaction_content_ttl_days
    concurrency: int = _compaction_concurrency_default()

    def expires_delta(self) -> timedelta | None:
        if self.content_ttl_days <= 0:
            return None
        return timedelta(days=self.content_ttl_days)


@dataclass(frozen=True)
class MemorySearchConfig:
    enabled: bool = settings.memory_search_enabled
    vector_weight: float = settings.memory_search_vector_weight
    keyword_weight: float = settings.memory_search_keyword_weight
    temporal_decay_enabled: bool = settings.memory_search_temporal_decay_enabled
    half_life_days: int = settings.memory_search_half_life_days
    mmr_lambda: float = settings.memory_search_mmr_lambda
    max_results: int = settings.memory_search_max_results
    max_snippet_chars: int = settings.memory_search_max_snippet_chars
    index_name: str = settings.memory_search_index_name


@dataclass(frozen=True)
class ContextMemoryLLMConfig:
    turn_notes_model: str = "context_memory_legacy_json_model"
    summary_model: str = "context_memory_legacy_json_model"
