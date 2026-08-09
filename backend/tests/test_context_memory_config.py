from __future__ import annotations

import pytest

from context_memory.config import (
    CompactionConfig,
    MemorySearchConfig,
    TokenBudgetConfig,
)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("room_context_pct", -0.01),
        ("conversation_history_pct", 1.01),
        ("current_task_pct", float("nan")),
        ("current_task_pct", float("inf")),
    ],
)
def test_token_budget_rejects_invalid_percentages(field_name: str, value: float):
    with pytest.raises(ValueError, match=field_name):
        TokenBudgetConfig(**{field_name: value})


def test_token_budget_rejects_allocation_sum_over_one():
    with pytest.raises(ValueError, match="percentages must not exceed 1"):
        TokenBudgetConfig(
            room_context_pct=0.4,
            conversation_history_pct=0.4,
            current_task_pct=0.3,
        )


@pytest.mark.parametrize(
    "field_name",
    [
        "model_context_window",
        "system_prompt",
        "tool_schemas",
        "response_reserve",
    ],
)
def test_token_budget_rejects_negative_token_values(field_name: str):
    with pytest.raises(ValueError, match=field_name):
        TokenBudgetConfig(**{field_name: -1})


def test_token_budget_accepts_zero_values_and_percentage_boundaries():
    config = TokenBudgetConfig(
        model_context_window=0,
        system_prompt=0,
        tool_schemas=0,
        response_reserve=0,
        room_context_pct=0,
        conversation_history_pct=0,
        current_task_pct=1,
    )

    assert config.available_for_content == 0


@pytest.mark.parametrize(
    "field_name",
    [
        "max_full_turns",
        "max_total_tokens",
        "preserve_recent_turns",
        "content_ttl_days",
    ],
)
def test_compaction_rejects_negative_thresholds(field_name: str):
    with pytest.raises(ValueError, match=field_name):
        CompactionConfig(**{field_name: -1})


def test_compaction_rejects_non_positive_concurrency():
    with pytest.raises(ValueError, match="concurrency must be at least 1"):
        CompactionConfig(concurrency=0)


def test_compaction_accepts_zero_thresholds_preserve_and_ttl():
    config = CompactionConfig(
        max_full_turns=0,
        max_total_tokens=0,
        preserve_recent_turns=0,
        content_ttl_days=0,
        concurrency=1,
    )

    assert config.max_full_turns == 0
    assert config.expires_delta() is None


@pytest.mark.parametrize(
    "field_name",
    ["half_life_days", "max_results", "max_candidates", "max_snippet_chars"],
)
def test_memory_search_rejects_non_positive_limits(field_name: str):
    with pytest.raises(ValueError, match=field_name):
        MemorySearchConfig(**{field_name: 0})


def test_memory_search_accepts_minimum_positive_values():
    config = MemorySearchConfig(
        half_life_days=1,
        max_results=1,
        max_candidates=1,
        max_snippet_chars=1,
    )

    assert config.half_life_days == 1
