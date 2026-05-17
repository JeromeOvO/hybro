"""
Runtime configuration classes for the Context Memory System.

These classes load values from environment variables via config/settings.py
and provide typed, property-based access.

See CONTEXT_MEMORY_SYSTEM_DESIGN.md §14.3 for specification.
"""

from config.settings import get_memory_search_index_name, settings


class TokenBudget:
    """Token allocation for context assembly. Loaded from env."""

    @property
    def model_context_window(self) -> int:
        return settings.context_model_window

    @property
    def system_prompt(self) -> int:
        return settings.context_system_prompt_tokens

    @property
    def tool_schemas(self) -> int:
        return settings.context_tool_schema_tokens

    @property
    def response_reserve(self) -> int:
        return settings.context_response_reserve_tokens

    @property
    def room_context_pct(self) -> float:
        return settings.context_room_pct

    @property
    def conversation_history_pct(self) -> float:
        return settings.context_history_pct

    @property
    def current_task_pct(self) -> float:
        return settings.context_task_pct

    @property
    def available_for_content(self) -> int:
        return self.model_context_window - (
            self.system_prompt + self.tool_schemas + self.response_reserve
        )

    @property
    def room_context_tokens(self) -> int:
        """Tokens allocated for room context (facts, agent roster)."""
        return int(self.available_for_content * self.room_context_pct)

    @property
    def conversation_history_tokens(self) -> int:
        """Tokens allocated for conversation history."""
        return int(self.available_for_content * self.conversation_history_pct)

    @property
    def current_task_tokens(self) -> int:
        """Tokens allocated for current task/request."""
        return int(self.available_for_content * self.current_task_pct)

    def get_budget_summary(self) -> dict[str, int]:
        """Get a summary of token allocations."""
        return {
            "model_context_window": self.model_context_window,
            "system_prompt": self.system_prompt,
            "tool_schemas": self.tool_schemas,
            "response_reserve": self.response_reserve,
            "available_for_content": self.available_for_content,
            "room_context": self.room_context_tokens,
            "conversation_history": self.conversation_history_tokens,
            "current_task": self.current_task_tokens,
        }


class CompactionConfig:
    """
    Compaction configuration. Loaded from env.

    NOTE: This is LOSSLESS compaction (pointer-based), NOT summarization.

    Current implementation: Text content stored in MongoDB
    Future extension: Binary content (images, files, video) will use S3 (see Section 6.8)
    """

    @property
    def enabled(self) -> bool:
        return settings.compaction_enabled

    @property
    def max_full_turns(self) -> int:
        """Max turns to keep in FULL representation."""
        return settings.compaction_max_full_turns

    @property
    def max_total_tokens(self) -> int:
        """Trigger compaction when full turns exceed this token count."""
        return settings.compaction_max_total_tokens

    @property
    def preserve_recent_turns(self) -> int:
        """Always keep this many recent turns in FULL representation."""
        return settings.compaction_preserve_recent

    @property
    def content_ttl_days(self) -> int:
        """TTL for stored content (0 = forever)."""
        return settings.compaction_content_ttl_days


class MemorySearchConfig:
    """Memory search configuration. Loaded from env."""

    @property
    def enabled(self) -> bool:
        return settings.memory_search_enabled

    @property
    def vector_weight(self) -> float:
        return settings.memory_search_vector_weight

    @property
    def keyword_weight(self) -> float:
        return settings.memory_search_keyword_weight

    @property
    def temporal_decay_enabled(self) -> bool:
        return settings.memory_search_temporal_decay_enabled

    @property
    def half_life_days(self) -> int:
        return settings.memory_search_half_life_days

    @property
    def mmr_lambda(self) -> float:
        return settings.memory_search_mmr_lambda

    @property
    def max_results(self) -> int:
        return settings.memory_search_max_results

    @property
    def max_snippet_chars(self) -> int:
        return settings.memory_search_max_snippet_chars

    @property
    def index_name(self) -> str:
        return get_memory_search_index_name(settings)


# Singleton instances
token_budget = TokenBudget()
compaction_config = CompactionConfig()
memory_search_config = MemorySearchConfig()
