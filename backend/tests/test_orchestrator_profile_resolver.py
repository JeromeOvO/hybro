from __future__ import annotations

from hashlib import sha256

import pytest

from common.config.settings import Settings
from execution.adapters.profiles import (
    FAST_ORCHESTRATOR_SYSTEM_PROMPT,
    ULTIMATE_ORCHESTRATOR_SYSTEM_PROMPT,
    OrchestratorProfileResolutionError,
    OrchestratorProfileResolver,
    PromptAssetRegistry,
)
from execution.orchestrator.profiles import UnsupportedProviderCapabilities
from llm_gateway.errors import LLMModelRoutingError
from llm_gateway.model_registry import ModelRouteInfo


def _route(**updates) -> ModelRouteInfo:
    values = {
        "logical_name": "supervisor_model",
        "provider": "openai",
        "model_id": "gpt-5-mini",
        "api": "chat_completions",
        "supports_native_tools": True,
        "supports_provider_strict_schema": True,
        "supports_local_structured_action": False,
        "context_window": 128000,
        "max_output_tokens": 8192,
        "default_temperature": None,
        "timeout_seconds": 60.0,
        "max_provider_retries": 1,
        "supported_thinking_levels": (),
    }
    values.update(updates)
    return ModelRouteInfo(**values)


class FakeModelRegistry:
    def __init__(self, routes: dict[str, ModelRouteInfo] | None = None) -> None:
        self._routes = dict(routes or {})

    def get_route_configuration(self, logical_name: str) -> ModelRouteInfo:
        try:
            return self._routes[logical_name]
        except KeyError as exc:
            raise LLMModelRoutingError(
                f"No orchestrator model route configured for {logical_name!r}"
            ) from exc


def test_parameter_table_defaults_are_pinned():
    fields = Settings.model_fields
    assert fields["orchestrator_fast_model_route"].default == "supervisor_model"
    assert fields["orchestrator_fast_prompt_id"].default == "orchestrator_fast"
    assert fields["orchestrator_fast_max_model_turns"].default == 6
    assert fields["orchestrator_fast_max_agent_calls"].default == 10
    assert fields["orchestrator_fast_max_parallel_calls"].default == 3
    assert fields["orchestrator_fast_initial_routing"].default == (
        "explicit_agent_first"
    )
    assert fields["orchestrator_fast_finalization"].default == "pass_through"

    assert fields["orchestrator_ultimate_model_route"].default == "supervisor_model"
    assert fields["orchestrator_ultimate_prompt_id"].default == "orchestrator_ultimate"
    assert fields["orchestrator_ultimate_max_model_turns"].default == 12
    assert fields["orchestrator_ultimate_max_agent_calls"].default == 20
    assert fields["orchestrator_ultimate_max_parallel_calls"].default == 4
    assert fields["orchestrator_ultimate_initial_routing"].default == (
        "explicit_agent_first"
    )
    assert fields["orchestrator_ultimate_finalization"].default == "pass_through"


def test_orchestrator_prompts_require_specialist_dispatch_before_user_clarify():
    for prompt in (
        FAST_ORCHESTRATOR_SYSTEM_PROMPT,
        ULTIMATE_ORCHESTRATOR_SYSTEM_PROMPT,
    ):
        assert "DELEGATION FIRST" in prompt
        assert "MUST call that agent on the first turn" in prompt
        assert "Never ask the user instead of calling a matching agent" in prompt
        assert "Missing details alone are not a reason to skip tools" in prompt


def test_fast_and_ultimate_profiles_resolve_from_defaults():
    registry = FakeModelRegistry({"supervisor_model": _route()})
    resolver = OrchestratorProfileResolver(
        model_registry=registry, settings_obj=Settings()
    )

    fast = resolver.resolve("fast")
    ultimate = resolver.resolve("ultimate")

    assert fast.profile_id == "fast"
    assert fast.model.route == "supervisor_model"
    assert fast.model.model_id == "gpt-5-mini"
    assert fast.prompt.prompt_id == "orchestrator_fast"
    assert fast.prompt.rendered_system_prompt == FAST_ORCHESTRATOR_SYSTEM_PROMPT
    assert fast.initial_routing == "explicit_agent_first"
    assert fast.finalization == "pass_through"
    assert fast.max_model_turns == 6
    assert fast.max_agent_calls == 10
    assert fast.max_parallel_calls == 3

    assert ultimate.profile_id == "ultimate"
    assert ultimate.prompt.prompt_id == "orchestrator_ultimate"
    assert ultimate.initial_routing == "explicit_agent_first"
    assert ultimate.finalization == "pass_through"
    assert ultimate.max_model_turns == 12
    assert ultimate.max_agent_calls == 20
    assert ultimate.max_parallel_calls == 4


def test_missing_model_route_fails_with_clear_message():
    resolver = OrchestratorProfileResolver(
        model_registry=FakeModelRegistry(), settings_obj=Settings()
    )
    with pytest.raises(
        OrchestratorProfileResolutionError, match="No orchestrator model route"
    ):
        resolver.resolve("fast")


def test_missing_prompt_asset_fails():
    registry = FakeModelRegistry({"supervisor_model": _route()})
    settings = Settings(orchestrator_fast_prompt_id="missing_prompt")
    resolver = OrchestratorProfileResolver(
        model_registry=registry, settings_obj=settings
    )
    with pytest.raises(OrchestratorProfileResolutionError, match="prompt asset"):
        resolver.resolve("fast")


def test_route_without_any_tool_capability_is_rejected():
    registry = FakeModelRegistry(
        {
            "supervisor_model": _route(
                supports_native_tools=False,
                supports_provider_strict_schema=False,
                supports_local_structured_action=False,
            )
        }
    )
    resolver = OrchestratorProfileResolver(
        model_registry=registry, settings_obj=Settings()
    )
    with pytest.raises(UnsupportedProviderCapabilities):
        resolver.resolve("fast")


def test_prompt_digest_mismatch_is_rejected():
    registry = FakeModelRegistry({"supervisor_model": _route()})
    prompts = PromptAssetRegistry()
    prompts.register(
        "orchestrator_fast",
        FAST_ORCHESTRATOR_SYSTEM_PROMPT,
        content_digest="0" * 64,
    )
    resolver = OrchestratorProfileResolver(
        model_registry=registry, prompt_registry=prompts, settings_obj=Settings()
    )
    with pytest.raises(ValueError, match="digest"):
        resolver.resolve("fast")


def test_prompt_digest_match_resolves_and_freezes_digest():
    digest = sha256(FAST_ORCHESTRATOR_SYSTEM_PROMPT.encode()).hexdigest()
    prompts = PromptAssetRegistry()
    prompts.register(
        "orchestrator_fast",
        FAST_ORCHESTRATOR_SYSTEM_PROMPT,
        content_digest=digest,
    )
    resolver = OrchestratorProfileResolver(
        model_registry=FakeModelRegistry({"supervisor_model": _route()}),
        prompt_registry=prompts,
        settings_obj=Settings(),
    )

    fast = resolver.resolve("fast")
    assert fast.prompt.content_digest == digest
