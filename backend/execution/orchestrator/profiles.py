"""Pure profile and provider-capability snapshot resolution."""

from __future__ import annotations

from hashlib import sha256
from typing import Literal

from pydantic import Field, model_validator

from .models import (
    ContractModel,
    OrchestratorProfile,
    PromptSnapshot,
    ResolvedModelSnapshot,
)


class UnsupportedProviderCapabilities(ValueError):
    """Raised when a route cannot provide a supported tool strategy."""


class ModelRouteConfiguration(ContractModel):
    route: str
    provider: Literal["openai", "deepseek"]
    model_id: str
    api: Literal["chat_completions", "responses"]
    supports_native_tools: bool = False
    supports_provider_strict_schema: bool = False
    supports_local_structured_action: bool = False
    context_window: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    temperature: float | None = None
    provider_timeout_seconds: float = Field(gt=0)
    max_provider_retries: int = Field(ge=0)
    supported_thinking_levels: list[str] = Field(default_factory=list)


class PromptConfiguration(ContractModel):
    prompt_id: str
    version: str
    rendered_system_prompt: str
    content_digest: str | None = None


class ProfileConfiguration(ContractModel):
    profile_id: Literal["fast", "ultimate"]
    thinking_level: str | None = None
    max_model_turns: int = Field(gt=0)
    grace_model_turns: int = Field(ge=0)
    max_agent_calls: int = Field(gt=0)
    max_parallel_calls: int = Field(gt=0)
    max_transport_retries_per_call: int = Field(ge=0)
    max_provider_retries_total: int = Field(default=4, ge=0)
    max_input_tokens_total: int | None = Field(default=None, gt=0)
    max_output_tokens_total: int | None = Field(default=None, gt=0)
    max_compactions: int = Field(ge=0)
    deadline_seconds: float = Field(gt=0)
    # Reserved profile dimensions, frozen per Run but not yet consumed by the
    # kernel. Production composition pins `explicit_agent_first` (the candidate
    # scope is pre-filtered by the API before the Run starts) and
    # `pass_through` (the final assistant message is delivered unchanged).
    # `model_select` and `synthesize` are deferred product capabilities; do not
    # treat these fields as active behavior, and update the cutover contract
    # test when either becomes consumed.
    initial_routing: Literal["explicit_agent_first", "model_select"]
    tool_execution: Literal["sequential", "parallel"]
    finalization: Literal["pass_through", "light", "synthesize"]

    @model_validator(mode="after")
    def _parallelism_fits_call_budget(self) -> ProfileConfiguration:
        if self.max_parallel_calls > self.max_agent_calls:
            raise ValueError("max_parallel_calls cannot exceed max_agent_calls")
        return self


def resolve_model_snapshot(
    config: ModelRouteConfiguration,
    *,
    preferred_strategy: Literal["native", "structured_action"] | None = None,
) -> ResolvedModelSnapshot:
    """Freeze a model route and reject unsupported tool capabilities."""

    if preferred_strategy not in {None, "native", "structured_action"}:
        raise UnsupportedProviderCapabilities(
            f"unsupported tool strategy {preferred_strategy!r}"
        )
    if preferred_strategy == "native":
        if not config.supports_native_tools:
            raise UnsupportedProviderCapabilities(
                f"model route {config.route!r} does not support native tools"
            )
        strategy: Literal["native", "structured_action"] = "native"
    elif preferred_strategy == "structured_action":
        if not (
            config.supports_provider_strict_schema
            or config.supports_local_structured_action
        ):
            raise UnsupportedProviderCapabilities(
                f"model route {config.route!r} does not support validated "
                "structured actions"
            )
        strategy = "structured_action"
    elif config.supports_native_tools:
        strategy = "native"
    elif (
        config.supports_provider_strict_schema
        or config.supports_local_structured_action
    ):
        strategy = "structured_action"
    else:
        raise UnsupportedProviderCapabilities(
            f"model route {config.route!r} supports neither native tools nor "
            "structured actions"
        )

    validation = (
        "unsupported"
        if strategy == "native"
        else ("provider_strict" if config.supports_provider_strict_schema else "local")
    )
    return ResolvedModelSnapshot(
        **config.model_dump(),
        structured_action_validation=validation,
        tool_strategy=strategy,
    )


def resolve_prompt_snapshot(config: PromptConfiguration) -> PromptSnapshot:
    """Freeze rendered prompt content and verify an optional declared digest."""

    digest = sha256(config.rendered_system_prompt.encode()).hexdigest()
    if config.content_digest is not None and config.content_digest != digest:
        raise ValueError("configured prompt digest does not match rendered content")
    return PromptSnapshot(
        prompt_id=config.prompt_id,
        version=config.version,
        content_digest=digest,
        rendered_system_prompt=config.rendered_system_prompt,
    )


def resolve_profile_snapshot(
    config: ProfileConfiguration,
    *,
    model: ModelRouteConfiguration,
    prompt: PromptConfiguration,
    preferred_strategy: Literal["native", "structured_action"] | None = None,
) -> OrchestratorProfile:
    """Resolve an immutable Run-owned profile snapshot from test/runtime config."""

    return OrchestratorProfile(
        **config.model_dump(),
        model=resolve_model_snapshot(model, preferred_strategy=preferred_strategy),
        prompt=resolve_prompt_snapshot(prompt),
    )
