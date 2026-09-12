from __future__ import annotations

import pytest
from pydantic import ValidationError

from common.config.loader import Settings

RUNTIME_CONFIG_ENV_VARS = (
    "FEATURE_RUN_EVENT_SSE",
    "ORCHESTRATOR_WORKER_INTERVAL_SECONDS",
    "ORCHESTRATION_OUTCOME_GUARDRAILS",
    "SUPERVISOR_MAX_STEPS",
    "RUN_WATCHDOG_STALE_MINUTES",
    "AGENT_HEALTH_CHECK_INTERVAL",
    "COMPACTION_CONCURRENCY",
)


def _clear_runtime_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in RUNTIME_CONFIG_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_removed_public_gateway_settings_are_not_exposed():
    removed_fields = {
        "discovery_default_limit",
        "discovery_rate_limit_per_key",
        "discovery_rate_limit_global",
        "hybro_timeout_seconds",
        "gateway_base_url",
        "gateway_rate_limit_per_key",
        "gateway_rate_limit_global",
    }

    assert removed_fields.isdisjoint(Settings.model_fields)
    assert {
        "local_agent_discovery_enabled",
        "local_agent_discovery_host",
        "local_agent_discovery_port_start",
        "local_agent_discovery_port_end",
        "local_agent_discovery_interval_seconds",
        "local_agent_discovery_connect_timeout_seconds",
        "local_agent_discovery_probe_timeout_seconds",
        "local_agent_discovery_excluded_ports",
    }.issubset(Settings.model_fields)


def test_canonical_lifecycle_has_no_runtime_admission_or_worker_switches():
    settings = Settings()

    assert not hasattr(settings, "feature_canonical_turn_lifecycle")
    assert not hasattr(settings, "orchestrator_projection_enabled")
    assert not hasattr(settings, "orchestrator_recovery_enabled")


def test_local_agent_discovery_excluded_ports_parses_compose_projection():
    """Compose projects the stack's own agent ports as one comma-separated value."""
    settings = Settings.model_validate(
        {"local_agent_discovery_excluded_ports": "7001,6002,7002,7003"}
    )
    assert settings.local_agent_discovery_excluded_ports == frozenset(
        {7001, 6002, 7002, 7003}
    )
    assert Settings().local_agent_discovery_excluded_ports == frozenset()


def test_feature_run_event_sse_defaults_on(monkeypatch):
    monkeypatch.delenv("FEATURE_RUN_EVENT_SSE", raising=False)
    settings = Settings()
    assert settings.feature_run_event_sse is True


def test_orchestration_outcome_guardrails_defaults_on(monkeypatch):
    monkeypatch.delenv("ORCHESTRATION_OUTCOME_GUARDRAILS", raising=False)
    settings = Settings()
    assert settings.orchestration_outcome_guardrails is True


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", False),
        ("0", False),
        ("false", False),
        ("1", True),
        ("true", True),
    ],
)
def test_orchestration_outcome_guardrails_parsing(
    raw: str,
    expected: bool,
) -> None:
    settings = Settings(orchestration_outcome_guardrails=raw)

    assert settings.orchestration_outcome_guardrails is expected


def test_orchestration_outcome_guardrails_ignores_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_runtime_config_env(monkeypatch)
    monkeypatch.setenv("ORCHESTRATION_OUTCOME_GUARDRAILS", "false")

    settings = Settings()

    assert settings.orchestration_outcome_guardrails is True


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
    settings = Settings(feature_run_event_sse=raw)

    assert settings.feature_run_event_sse is expected


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_blank_orchestrator_thinking_level_normalizes_to_none(raw: str | None) -> None:
    settings = Settings(
        orchestrator_fast_thinking_level=raw,
        orchestrator_ultimate_thinking_level=raw,
    )

    assert settings.orchestrator_fast_thinking_level is None
    assert settings.orchestrator_ultimate_thinking_level is None


def test_runtime_config_unification_explicit_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SUPERVISOR_MAX_STEPS", "13")
    monkeypatch.setenv("RUN_WATCHDOG_STALE_MINUTES", "31")
    monkeypatch.setenv("AGENT_HEALTH_CHECK_INTERVAL", "120")
    monkeypatch.setenv("COMPACTION_CONCURRENCY", "7")

    settings = Settings(
        supervisor_max_steps=13,
        run_watchdog_stale_minutes=31,
        agent_health_check_interval=120,
        compaction_concurrency=7,
    )

    assert settings.supervisor_max_steps == 13
    assert settings.run_watchdog_stale_minutes == 31
    assert settings.agent_health_check_interval == 120
    assert settings.compaction_concurrency == 7


def test_a2a_inline_file_dispatch_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("A2A_INLINE_FILE_MAX_RAW_BYTES", raising=False)
    monkeypatch.delenv("A2A_INLINE_MESSAGE_MAX_ENCODED_BYTES", raising=False)

    settings = Settings()

    assert settings.a2a_inline_file_max_raw_bytes == 5 * 1024 * 1024
    assert settings.a2a_inline_message_max_encoded_bytes == 6_990_508


def test_a2a_inline_file_dispatch_explicit_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("A2A_INLINE_FILE_MAX_RAW_BYTES", "1024")
    monkeypatch.setenv("A2A_INLINE_MESSAGE_MAX_ENCODED_BYTES", "2048")

    settings = Settings(
        a2a_inline_file_max_raw_bytes=1024, a2a_inline_message_max_encoded_bytes=2048
    )

    assert settings.a2a_inline_file_max_raw_bytes == 1024
    assert settings.a2a_inline_message_max_encoded_bytes == 2048


@pytest.mark.parametrize(
    ("raw_file_limit", "raw_message_limit", "expected_raw", "expected_encoded"),
    [
        ("", "", 5 * 1024 * 1024, 6_990_508),
        ("0", "0", 1, 4),
        ("-9", "-1", 1, 4),
        ("3", "0", 3, 4),
        ("4", "", 4, 8),
        ("bad", "also-bad", 5 * 1024 * 1024, 6_990_508),
    ],
)
def test_a2a_inline_file_dispatch_normalizes_limits(
    raw_file_limit: str,
    raw_message_limit: str,
    expected_raw: int,
    expected_encoded: int,
) -> None:
    settings = Settings(
        a2a_inline_file_max_raw_bytes=raw_file_limit,
        a2a_inline_message_max_encoded_bytes=raw_message_limit,
    )

    assert settings.a2a_inline_file_max_raw_bytes == expected_raw
    assert settings.a2a_inline_message_max_encoded_bytes == expected_encoded


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
    settings = Settings(compaction_concurrency=raw)

    assert settings.compaction_concurrency == expected


def test_webhook_signing_key_allows_disabled_default() -> None:
    settings = Settings(webhook_signing_key="")

    assert settings.webhook_signing_key == ""


def test_webhook_signing_key_accepts_at_least_32_bytes() -> None:
    signing_key = "k" * 32

    settings = Settings(webhook_signing_key=f" {signing_key} ")

    assert settings.webhook_signing_key == signing_key


def test_webhook_signing_key_rejects_short_configured_value() -> None:
    with pytest.raises(
        ValidationError,
        match="WEBHOOK_SIGNING_KEY must be at least 32 bytes",
    ):
        Settings(webhook_signing_key="too-short")
