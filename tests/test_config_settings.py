from __future__ import annotations

import pytest

from common.config.settings import (
    MEMORY_SEARCH_INDEX_NAME_DEFAULT,
    PINECONE_INDEX_NAME_DEFAULT,
    Settings,
)

RUNTIME_CONFIG_ENV_VARS = (
    "FEATURE_RUN_DUAL_WRITE",
    "FEATURE_RUN_EVENT_SSE",
    "FEATURE_RUN_WATCHDOG",
    "SUPERVISOR_MAX_STEPS",
    "RUN_WATCHDOG_STALE_MINUTES",
    "MATCH_VECTOR_WEIGHT",
    "MATCH_CAPABILITY_WEIGHT",
    "MATCH_DEBATE_THRESHOLD",
    "MATCH_GAP_THRESHOLD",
    "MATCH_QUALITY_THRESHOLD",
    "AGENT_HEALTH_CHECK_INTERVAL",
    "COMPACTION_CONCURRENCY",
    "PINECONE_INDEX_NAME",
    "MEMORY_SEARCH_INDEX_NAME",
)


def _clear_runtime_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in RUNTIME_CONFIG_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_runtime_config_unification_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_runtime_config_env(monkeypatch)
    settings = Settings(_env_file=None)

    assert settings.feature_run_dual_write is True
    assert settings.feature_run_event_sse is False
    assert settings.feature_run_watchdog is True
    assert settings.supervisor_max_steps == 8
    assert settings.run_watchdog_stale_minutes == 90
    assert settings.match_vector_weight == 0.85
    assert settings.match_capability_weight == 0.15
    assert settings.match_debate_threshold == 0.3
    assert settings.match_gap_threshold == 0.15
    assert settings.match_quality_threshold == 0.4
    assert settings.agent_health_check_interval == 3600
    assert settings.compaction_concurrency == 5
    assert settings.pinecone_index_name == PINECONE_INDEX_NAME_DEFAULT
    assert settings.memory_search_index_name == MEMORY_SEARCH_INDEX_NAME_DEFAULT


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", True),
        ("0", False),
        ("false", False),
        ("no", False),
        ("off", False),
        ("1", True),
        ("true", True),
        ("yes", True),
        ("on", True),
        ("garbage", True),
    ],
)
def test_feature_run_dual_write_parses_legacy_values(raw: str, expected: bool) -> None:
    settings = Settings(_env_file=None, feature_run_dual_write=raw)

    assert settings.feature_run_dual_write is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", False),
        ("0", False),
        ("false", False),
        ("no", False),
        ("off", False),
        ("1", True),
        ("true", True),
        ("yes", True),
        ("on", True),
        ("garbage", False),
    ],
)
def test_feature_run_event_sse_parses_legacy_values(raw: str, expected: bool) -> None:
    settings = Settings(_env_file=None, feature_run_event_sse=raw)

    assert settings.feature_run_event_sse is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", True),
        ("0", False),
        ("false", False),
        ("no", True),
        ("off", False),
        ("1", True),
        ("true", True),
        ("yes", True),
        ("on", True),
        ("garbage", True),
    ],
)
def test_feature_run_watchdog_parses_legacy_values(raw: str, expected: bool) -> None:
    settings = Settings(_env_file=None, feature_run_watchdog=raw)

    assert settings.feature_run_watchdog is expected


def test_runtime_config_unification_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPERVISOR_MAX_STEPS", "13")
    monkeypatch.setenv("RUN_WATCHDOG_STALE_MINUTES", "31")
    monkeypatch.setenv("MATCH_VECTOR_WEIGHT", "0.91")
    monkeypatch.setenv("MATCH_CAPABILITY_WEIGHT", "0.09")
    monkeypatch.setenv("MATCH_DEBATE_THRESHOLD", "0.44")
    monkeypatch.setenv("MATCH_GAP_THRESHOLD", "0.22")
    monkeypatch.setenv("MATCH_QUALITY_THRESHOLD", "0.66")
    monkeypatch.setenv("AGENT_HEALTH_CHECK_INTERVAL", "120")
    monkeypatch.setenv("COMPACTION_CONCURRENCY", "7")
    monkeypatch.setenv("PINECONE_INDEX_NAME", "agents-custom")
    monkeypatch.setenv("MEMORY_SEARCH_INDEX_NAME", "memory-custom")

    settings = Settings(_env_file=None)

    assert settings.supervisor_max_steps == 13
    assert settings.run_watchdog_stale_minutes == 31
    assert settings.match_vector_weight == 0.91
    assert settings.match_capability_weight == 0.09
    assert settings.match_debate_threshold == 0.44
    assert settings.match_gap_threshold == 0.22
    assert settings.match_quality_threshold == 0.66
    assert settings.agent_health_check_interval == 120
    assert settings.compaction_concurrency == 7
    assert settings.pinecone_index_name == "agents-custom"
    assert settings.memory_search_index_name == "memory-custom"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", 5),
        ("bad", 5),
        ("0", 1),
        ("-2", 1),
        ("9", 9),
    ],
)
def test_compaction_concurrency_preserves_legacy_fallbacks(
    raw: str,
    expected: int,
) -> None:
    settings = Settings(_env_file=None, compaction_concurrency=raw)

    assert settings.compaction_concurrency == expected


def test_blank_index_names_fall_back_to_defaults() -> None:
    settings = Settings(
        _env_file=None,
        pinecone_index_name="",
        memory_search_index_name="",
    )

    assert settings.pinecone_index_name == PINECONE_INDEX_NAME_DEFAULT
    assert settings.memory_search_index_name == MEMORY_SEARCH_INDEX_NAME_DEFAULT
