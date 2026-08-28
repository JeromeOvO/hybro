from __future__ import annotations

import pytest
from pydantic import ValidationError

from execution.orchestrator.lifecycle import SessionEvent
from execution.orchestrator.models import (
    FrozenToolCatalogEntry,
    FrozenToolCatalogSnapshot,
    OrchestratorRunState,
    ToolBindingRef,
    ToolDefinition,
)
from execution.orchestrator.public_projection import PublicProjectionTranslator
from execution.orchestrator.public_summaries import PublicSummaryRegistry
from execution.orchestrator.session import DefaultRunFactory

from ._orchestrator_helpers import (
    FixedClock,
    FixedIDs,
    make_run,
    session_config,
    user_message,
)


def test_unknown_tool_summary_is_deny_by_default():
    registry = PublicSummaryRegistry()
    private = {"secret_api_key": "never-leak", "nested": {"token": "raw"}}
    assert registry.input_summary("unknown", private) == {}
    assert registry.result_summary("unknown", private) == ""


def test_registered_builder_only_returns_declared_safe_fields():
    registry = PublicSummaryRegistry()
    registry.register(
        "weather",
        input_builder=lambda args: {"city": str(args.get("city") or "")},
        result_builder=lambda result: str(result.get("conditions") or ""),
    )
    arguments = {"city": "Shanghai", "api_key": "never-leak"}
    result = {"conditions": "sunny", "provider_token": "never-leak"}
    assert registry.input_summary("weather", arguments) == {"city": "Shanghai"}
    assert registry.result_summary("weather", result) == "sunny"


def test_catalog_registration_exposes_only_explicit_safe_summary_fields():
    registry = PublicSummaryRegistry(secret_values=("catalog-secret",))
    catalog = FrozenToolCatalogSnapshot(
        catalog_id="catalog-1",
        entries=[
            FrozenToolCatalogEntry(
                definition=ToolDefinition(
                    name="weather",
                    label="Weather Agent",
                    description="Weather",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "task": {"type": "string"},
                            "city": {
                                "type": "string",
                                "x-hybro-public-summary": True,
                            },
                            "api_key": {"type": "string"},
                        },
                    },
                    execution_mode="parallel",
                    side_effect_level="read",
                ),
                binding=ToolBindingRef(binding_id="binding-1", binding_digest="digest"),
            )
        ],
        created_at=FixedClock().now(),
    )

    registry.register_catalog(catalog)

    assert registry.input_summary(
        "weather",
        {"task": "Current weather", "city": "Shanghai", "api_key": "secret"},
        catalog=catalog,
    ) == {"task": "Current weather", "city": "Shanghai"}
    assert (
        registry.result_summary("weather", "Sunny catalog-secret", catalog=catalog)
        == "Sunny [REDACTED]"
    )
    assert (
        registry.input_summary("not-in-catalog", {"task": "private"}, catalog=catalog)
        == {}
    )


def _weather_catalog(
    catalog_id: str,
    *,
    public_city: bool,
) -> FrozenToolCatalogSnapshot:
    city_schema: dict[str, object] = {"type": "string"}
    if public_city:
        city_schema["x-hybro-public-summary"] = True
    return FrozenToolCatalogSnapshot(
        catalog_id=catalog_id,
        entries=[
            FrozenToolCatalogEntry(
                definition=ToolDefinition(
                    name="weather",
                    label="Weather Agent",
                    description="Weather",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "task": {"type": "string"},
                            "city": city_schema,
                        },
                    },
                    execution_mode="parallel",
                    side_effect_level="read",
                ),
                binding=ToolBindingRef(
                    binding_id=f"binding-{catalog_id}",
                    binding_digest=f"digest-{catalog_id}",
                ),
            )
        ],
        created_at=FixedClock().now(),
    )


def test_catalog_scoping_never_inherits_a_broader_prior_allowlist():
    registry = PublicSummaryRegistry()
    broad = _weather_catalog("catalog-broad", public_city=True)
    narrow = _weather_catalog("catalog-narrow", public_city=False)
    same_identity_narrow = _weather_catalog("catalog-broad", public_city=False)
    registry.register_catalog(broad)
    registry.register_catalog(narrow)
    registry.register_catalog(same_identity_narrow)

    arguments = {"task": "Forecast", "city": "private-city"}
    assert registry.input_summary("weather", arguments, catalog=broad) == arguments
    assert registry.input_summary("weather", arguments, catalog=narrow) == {
        "task": "Forecast"
    }
    assert registry.input_summary(
        "weather", arguments, catalog=same_identity_narrow
    ) == {"task": "Forecast"}


def test_production_translator_selects_summary_builder_from_run_catalog():
    registry = PublicSummaryRegistry()
    broad = _weather_catalog("catalog-broad", public_city=True)
    narrow = _weather_catalog("catalog-narrow", public_city=False)
    registry.register_catalog(broad)
    registry.register_catalog(narrow)
    translator = PublicProjectionTranslator(
        lifecycle_family="canonical",
        summary_registry=registry,
    )
    event = SessionEvent(
        event_type="tool_execution_started",
        session_id="session-1",
        run_id="run-1",
        causation_id="user-1",
        sequence=1,
        timestamp=FixedClock().now(),
        payload={
            "internal_turn_id": "turn-1",
            "public_call_id": "call-1",
            "tool_name": "weather",
            "arguments": {"task": "Forecast", "city": "private-city"},
        },
        room_id="room-1",
        user_message_id="user-1",
        client_request_id="client-1",
        lifecycle_family="canonical",
    )

    broad_event = translator.translate(event, catalog=broad)
    narrow_event = translator.translate(event, catalog=narrow)

    assert broad_event is not None
    assert broad_event.payload["input"] == {
        "task": "Forecast",
        "city": "private-city",
    }
    assert narrow_event is not None
    assert narrow_event.payload["input"] == {"task": "Forecast"}


def test_new_runs_are_unconditionally_persisted_as_canonical_schema_v6():
    factory = DefaultRunFactory(clock=FixedClock(), id_factory=FixedIDs())
    run = factory.create_run(
        config=session_config(),
        message=user_message(),
        client_request_id="request-1",
    )
    assert run.schema_version == 6
    assert run.lifecycle_family == "canonical"


def test_canonical_admission_requires_nonempty_correlation():
    factory = DefaultRunFactory(clock=FixedClock(), id_factory=FixedIDs())
    with pytest.raises(ValueError, match="client_request_id"):
        factory.create_run(
            config=session_config(),
            message=user_message(),
            client_request_id="",
        )


def test_persisted_canonical_run_remains_canonical_after_restart():
    factory = DefaultRunFactory(clock=FixedClock(), id_factory=FixedIDs())
    persisted = factory.create_run(
        config=session_config(),
        message=user_message(),
        client_request_id="request-1",
    )

    restarted = OrchestratorRunState.model_validate(persisted.model_dump(mode="python"))
    assert restarted.lifecycle_family == "canonical"
    assert restarted.schema_version == 6


def test_new_live_run_schedules_generic_recovery_at_watchdog_boundary():
    run = DefaultRunFactory(clock=FixedClock(), id_factory=FixedIDs()).create_run(
        config=session_config(),
        message=user_message(),
        client_request_id="request-1",
    )
    assert run.recovery_claim.next_attempt_at == run.budget.deadline_at


def test_v5_legacy_and_v6_canonical_documents_dual_read():
    canonical = make_run()
    assert canonical.schema_version == 6
    assert canonical.lifecycle_family == "canonical"

    legacy_document = canonical.model_dump(mode="json")
    legacy_document.update(schema_version=5, lifecycle_family="legacy")
    for field in (
        "active_internal_turn_id",
        "active_assistant_message_id",
        "active_attempt",
        "greatest_public_text_offset",
    ):
        legacy_document.pop(field, None)
    legacy = OrchestratorRunState.model_validate(legacy_document)
    assert legacy.schema_version == 5
    assert legacy.lifecycle_family == "legacy"

    invalid = canonical.model_dump(mode="json")
    invalid["schema_version"] = 5
    with pytest.raises(
        ValidationError, match="canonical Runs require schema version 6"
    ):
        OrchestratorRunState.model_validate(invalid)
