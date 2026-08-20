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
    provider: str
    model_id: str
    api: str
    supports_native_tools: bool = False
    supports_strict_tools: bool = False
    supports_structured_actions: bool = False
    context_window: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    temperature: float | None = None
    provider_timeout_seconds: float = Field(gt=0)
    max_provider_retries: int = Field(ge=0)


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
    max_compactions: int = Field(ge=0)
    deadline_seconds: float = Field(gt=0)
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
        if not (config.supports_structured_actions and config.supports_strict_tools):
            raise UnsupportedProviderCapabilities(
                f"model route {config.route!r} does not support strict "
                "structured actions"
            )
        strategy = "structured_action"
    elif config.supports_native_tools:
        strategy = "native"
    elif config.supports_structured_actions and config.supports_strict_tools:
        strategy = "structured_action"
    else:
        raise UnsupportedProviderCapabilities(
            f"model route {config.route!r} supports neither native tools nor "
            "structured actions"
        )

    return ResolvedModelSnapshot(
        **config.model_dump(exclude={"supports_structured_actions"}),
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
