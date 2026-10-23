from __future__ import annotations

import pytest

from context_memory.config import CompactionConfig


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


def test_compaction_accepts_preserve_recent_turns_equal_to_max_full_turns():
    config = CompactionConfig(max_full_turns=3, preserve_recent_turns=3)

    assert config.preserve_recent_turns == config.max_full_turns


def test_compaction_rejects_preserve_recent_turns_above_max_full_turns():
    with pytest.raises(
        ValueError,
        match="preserve_recent_turns must not exceed max_full_turns",
    ):
        CompactionConfig(max_full_turns=3, preserve_recent_turns=4)
