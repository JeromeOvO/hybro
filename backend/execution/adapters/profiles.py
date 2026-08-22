"""Typed Fast/Ultimate orchestrator profile resolution.

Fast and Ultimate share one Kernel and differ only in resolved parameters
(system prompt, model route, token/tool/time budgets, tool-execution policy).
This module reads those parameters from typed ``Settings`` fields
(``orchestrator_fast_*`` / ``orchestrator_ultimate_*``) and resolves an
immutable ``OrchestratorProfile`` snapshot through the orchestrator's pure
``resolve_profile_snapshot`` function.
"""

from __future__ import annotations

from typing import Any, Literal

from execution.orchestrator.model_runtime import route_configuration_from_gateway
from execution.orchestrator.models import OrchestratorProfile
from execution.orchestrator.profiles import (
    ModelRouteConfiguration,
    ProfileConfiguration,
    PromptConfiguration,
    resolve_profile_snapshot,
)
from llm_gateway.errors import LLMModelRoutingError
from llm_gateway.model_registry import ModelRegistryImpl

ProfileId = Literal["fast", "ultimate"]

FAST_ORCHESTRATOR_SYSTEM_PROMPT = (
    "You are the Hybro orchestrator. You coordinate specialist A2A agents to "
    "fulfill the user's request; you never perform the work yourself.\n\n"
    "CANDIDATE SCOPE (closed world): Only the tools listed in this "
    "conversation are available. Choose by each agent's advertised "
    "capabilities. If no agent fits the step, answer directly and say why — "
    "never invent a capability.\n\n"
    "DELEGATION: One step at a time. Put every concrete fact the agent needs "
    "(names, numbers, requirements, exact field values) directly into the "
    "task argument; never assume the agent can see the conversation. Pass "
    "larger material through context_refs/artifact_refs.\n\n"
    "AGENT INPUT REQUESTS: If an agent replies that it needs information, "
    "first check the user's message, attachments, and previous agent "
    "outputs. If the information is already available, call the agent again "
    "with those facts included. Only ask the user when the information is "
    "user-only and blocks progress, and ask for exactly the missing items. "
    "Never repeat the same agent and task without new evidence.\n\n"
    "TRUTHFULNESS: Preserve agent outputs verbatim. Never generate or "
    "estimate anything an agent must produce. Synthesize only from returned "
    "facts; if a required fact is missing, say so explicitly.\n\n"
    "COMPLETION: When the goal is satisfied, or no tool can help, write the "
    "final answer and stop — no further tool call. The final answer is "
    "delivered unchanged."
)

ULTIMATE_ORCHESTRATOR_SYSTEM_PROMPT = (
    "You are the Hybro orchestrator. You coordinate specialist A2A agents to "
    "fulfill complex, multi-step requests; you never perform the work "
    "yourself.\n\n"
    "CANDIDATE SCOPE (closed world): Only the tools listed in this "
    "conversation are available. Choose by each agent's advertised "
    "capabilities. If no agent fits the step, answer directly and say why — "
    "never invent a capability.\n\n"
    "DELEGATION: Plan multi-step work, but act one step at a time and "
    "re-plan from the latest results. Prefer sequential delegation when one "
    "agent's output is another agent's input. Put every concrete fact the "
    "agent needs (names, numbers, requirements, exact field values) directly "
    "into the task argument; never assume the agent can see the "
    "conversation. Pass larger material through context_refs/artifact_refs.\n\n"
    "AGENT INPUT REQUESTS: If an agent replies that it needs information, "
    "first check the user's message, attachments, and previous agent "
    "outputs. If the information is already available, call the agent again "
    "with those facts included. Only ask the user when the information is "
    "user-only and blocks progress, and ask for exactly the missing items. "
    "Never repeat the same agent and task without new evidence.\n\n"
    "TRUTHFULNESS: Preserve agent outputs verbatim. Never generate or "
    "estimate anything an agent must produce (prices, quotes, decisions, "
    "documents). Synthesize only from returned facts; if a required fact is "
    "missing, say so explicitly.\n\n"
    "COMPLETION: When the goal is satisfied, or no tool can help, write the "
    "final answer and stop — no further tool call. The final answer is "
    "delivered unchanged."
)

DEFAULT_ORCHESTRATOR_PROMPTS = {
    "orchestrator_fast": FAST_ORCHESTRATOR_SYSTEM_PROMPT,
    "orchestrator_ultimate": ULTIMATE_ORCHESTRATOR_SYSTEM_PROMPT,
}


class OrchestratorProfileResolutionError(ValueError):
    """Adapter-level failure while resolving an orchestrator profile."""


class PromptAssetRegistry:
    """Minimal prompt asset store: ``prompt_id`` -> rendered system prompt.

    ``content_digest`` is optional; when present it is validated by the
    orchestrator's ``resolve_prompt_snapshot`` path.
    """

    def __init__(
        self,
        prompts: dict[str, str] | None = None,
        digests: dict[str, str] | None = None,
    ) -> None:
        self._prompts = dict(
            DEFAULT_ORCHESTRATOR_PROMPTS if prompts is None else prompts
        )
        self._digests = dict(digests or {})

    def register(
        self,
        prompt_id: str,
        rendered_system_prompt: str,
        *,
        content_digest: str | None = None,
    ) -> None:
        self._prompts[prompt_id] = rendered_system_prompt
        if content_digest is None:
            self._digests.pop(prompt_id, None)
        else:
            self._digests[prompt_id] = content_digest

    def get(self, prompt_id: str) -> str:
        try:
            return self._prompts[prompt_id]
        except KeyError as exc:
            raise OrchestratorProfileResolutionError(
                f"no orchestrator prompt asset configured for {prompt_id!r}"
            ) from exc

    def digest(self, prompt_id: str) -> str | None:
        return self._digests.get(prompt_id)


class OrchestratorProfileResolver:
    """Resolve a Fast/Ultimate profile from typed settings and gateway routes."""

    def __init__(
        self,
        *,
        model_registry: ModelRegistryImpl,
        prompt_registry: PromptAssetRegistry | None = None,
        settings_obj: Any | None = None,
    ) -> None:
        from common.config.settings import settings

        self._model_registry = model_registry
        self._prompt_registry = prompt_registry or PromptAssetRegistry()
        self._settings = settings_obj or settings

    def resolve(self, profile_id: ProfileId) -> OrchestratorProfile:
        return resolve_profile_snapshot(
            self._profile_configuration(profile_id),
            model=self._model_configuration(profile_id),
            prompt=self._prompt_configuration(profile_id),
        )

    def _setting(self, profile_id: ProfileId, name: str, default: Any) -> Any:
        return getattr(self._settings, f"orchestrator_{profile_id}_{name}", default)

    def _profile_configuration(self, profile_id: ProfileId) -> ProfileConfiguration:
        return ProfileConfiguration(
            profile_id=profile_id,
            thinking_level=self._setting(profile_id, "thinking_level", None),
            max_model_turns=self._setting(profile_id, "max_model_turns", 6),
            grace_model_turns=self._setting(profile_id, "grace_model_turns", 1),
            max_agent_calls=self._setting(profile_id, "max_agent_calls", 10),
            max_parallel_calls=self._setting(profile_id, "max_parallel_calls", 3),
            max_transport_retries_per_call=self._setting(
                profile_id, "max_transport_retries_per_call", 2
            ),
            max_compactions=self._setting(profile_id, "max_compactions", 2),
            deadline_seconds=self._setting(profile_id, "deadline_seconds", 300.0),
            initial_routing=self._setting(
                profile_id, "initial_routing", "explicit_agent_first"
            ),
            tool_execution=self._setting(profile_id, "tool_execution", "parallel"),
            finalization=self._setting(profile_id, "finalization", "pass_through"),
        )

    def _model_configuration(self, profile_id: ProfileId) -> ModelRouteConfiguration:
        logical_name = self._setting(profile_id, "model_route", None)
        if not logical_name:
            raise OrchestratorProfileResolutionError(
                f"no orchestrator model route configured for {profile_id!r}"
            )
        try:
            route = self._model_registry.get_route_configuration(logical_name)
        except LLMModelRoutingError as exc:
            raise OrchestratorProfileResolutionError(str(exc)) from exc
        return route_configuration_from_gateway(route)

    def _prompt_configuration(self, profile_id: ProfileId) -> PromptConfiguration:
        prompt_id = self._setting(profile_id, "prompt_id", None)
        if not prompt_id:
            raise OrchestratorProfileResolutionError(
                f"no orchestrator prompt configured for {profile_id!r}"
            )
        # prompt_version is currently decorative: PromptAssetRegistry is an
        # inline default registry with no versioned assets or digests; a real
        # prompt-asset source replaces it before the canary rollout.
        version = str(self._setting(profile_id, "prompt_version", "1"))
        return PromptConfiguration(
            prompt_id=prompt_id,
            version=version,
            rendered_system_prompt=self._prompt_registry.get(prompt_id),
            content_digest=self._prompt_registry.digest(prompt_id),
        )


__all__ = [
    "DEFAULT_ORCHESTRATOR_PROMPTS",
    "FAST_ORCHESTRATOR_SYSTEM_PROMPT",
    "ULTIMATE_ORCHESTRATOR_SYSTEM_PROMPT",
    "OrchestratorProfileResolutionError",
    "OrchestratorProfileResolver",
    "PromptAssetRegistry",
    "ProfileId",
]
