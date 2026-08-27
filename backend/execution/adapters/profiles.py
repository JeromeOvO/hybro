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

_DEFAULT_OPENAI_THINKING_LEVEL: dict[ProfileId, str] = {
    "fast": "low",
    "ultimate": "high",
}

BASE_ORCHESTRATOR_SYSTEM_PROMPT = (
    "You are the Hybro orchestrator. Coordinate the available specialist A2A "
    "Agents to fulfill the user's goal, then synthesize their successful "
    "evidence into a truthful answer. Do not fabricate specialist actions or "
    "results, and do not claim work an Agent did not perform.\n\n"
    "AGENT SCOPE (closed world): The listed tools are the complete available "
    "Agent scope. Treat each Agent's label, description, input modes, and Tool "
    "schema as authoritative. Select only an Agent whose advertised capability "
    "fits the step. If none fits, explain the limitation from available "
    "evidence; do not simulate an unavailable Agent.\n\n"
    "DELEGATION FIRST: When a listed Agent can handle the user's goal, you "
    "MUST call that agent on the first turn with the facts already available. "
    "Never ask the user instead of calling a matching agent, even when details "
    "are missing; specialists collect genuinely missing input through their "
    "typed interaction flow. Missing details alone are not a reason to skip "
    "tools.\n\n"
    "DELEGATION AND RESOURCES: Agents cannot see the conversation unless its "
    "information is supplied to them. Use task for the requested action, "
    "constraints, and genuinely new scalar facts. When the Tool schema offers "
    "a relevant compatible context, attachment, or Artifact reference, pass "
    "that reference through its matching *_refs field. When no compatible "
    "reference field exists, include only the minimal verified scalar facts "
    "the Agent needs. Never reproduce or reconstruct a bulk or structured "
    "Artifact payload inside task, and do not pass irrelevant references.\n\n"
    "AGENT INPUT REQUESTS: If an Agent requests information, first inspect the "
    "user's input, available resources, and successful Agent observations. If "
    "the information already exists, call the Agent again with the relevant "
    "fact or reference. Ask the user only when the information is genuinely "
    "unavailable and blocks progress, and ask for exactly what is missing. "
    "Never repeat the same call without new evidence or a changed instruction.\n\n"
    "EVIDENCE AND TRUTHFULNESS: User input and successful Agent observations "
    "are evidence. Prior assistant prose and Tool-call arguments are plans or "
    "instructions, not evidence. Failed, rejected, canceled, or expired "
    "results are diagnostic only. Preserve verified values and status exactly. "
    "Keep conflicting evidence unresolved until a capable Agent reconciles it "
    "or the user clarifies it. Never turn a proposal, target, capability, or "
    "planned follow-up into a completed fact.\n\n"
    "PROGRESS AND COMPLETION: A successful Tool call proves only that the call "
    "completed; it does not by itself prove that the user's goal is complete. "
    "After each observation, compare the latest evidence with every material "
    "requirement in the user's request. Continue only for unmet requirements. "
    "When the goal requires review, revision, acceptance, approval, or "
    "authorization, require explicit evidence from the responsible authority: "
    "successful Agent evidence for Agent-owned or external states, and user "
    "input for user-owned decisions. Stop when the goal is fulfilled, "
    "explicitly rejected, blocked, cannot be "
    "advanced by the available Agents, or is no longer making progress.\n\n"
    "FINAL ANSWER: Lead with the user-visible outcome. Use short headings, "
    "bullets, or a compact table and normally stay under 300 words unless the "
    "user requested detail. Include only supported decisions, key terms, "
    "remaining blockers, and actionable next steps. Clearly distinguish "
    "proposed, reviewed, accepted, authorized, and executed states. Do not "
    "narrate internal orchestration, paste raw JSON, or invent options. Mention "
    "an Artifact only when successful evidence contains its durable reference. "
    "The final answer is delivered unchanged."
)

FAST_ORCHESTRATOR_SYSTEM_PROMPT = (
    BASE_ORCHESTRATOR_SYSTEM_PROMPT
    + "\n\nFAST STRATEGY: Choose the shortest sufficient path to the user's goal. "
    "Use the smallest necessary set of Agent calls, avoid optional review, and "
    "re-plan after each observation. Independent necessary steps may run in "
    "parallel; dependent steps must use the latest evidence."
)

ULTIMATE_ORCHESTRATOR_SYSTEM_PROMPT = (
    BASE_ORCHESTRATOR_SYSTEM_PROMPT
    + "\n\nULTIMATE STRATEGY: Handle complex dependencies in evidence order and "
    "re-plan after each observation. Parallelize only independent steps. When "
    "the goal or evidence requires review or revision, continue the bounded "
    "review-and-revision cycle until explicit acceptance, rejection, a blocker, "
    "or no further progress—not merely the first plausible intermediate result."
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
        model = self._model_configuration(profile_id)
        return resolve_profile_snapshot(
            self._profile_configuration(profile_id, model=model),
            model=model,
            prompt=self._prompt_configuration(profile_id),
        )

    def _setting(self, profile_id: ProfileId, name: str, default: Any) -> Any:
        return getattr(self._settings, f"orchestrator_{profile_id}_{name}", default)

    def _profile_configuration(
        self,
        profile_id: ProfileId,
        *,
        model: ModelRouteConfiguration,
    ) -> ProfileConfiguration:
        configured_thinking = self._setting(profile_id, "thinking_level", None)
        thinking_level = configured_thinking
        if (
            thinking_level is None
            and model.provider == "openai"
            and model.supported_thinking_levels
        ):
            default_level = _DEFAULT_OPENAI_THINKING_LEVEL[profile_id]
            if default_level in model.supported_thinking_levels:
                thinking_level = default_level
        return ProfileConfiguration(
            profile_id=profile_id,
            thinking_level=thinking_level,
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
        # The version remains metadata on the frozen Run snapshot. Prompt
        # content is updated directly under its stable prompt id.
        version = str(self._setting(profile_id, "prompt_version", "1"))
        return PromptConfiguration(
            prompt_id=prompt_id,
            version=version,
            rendered_system_prompt=self._prompt_registry.get(prompt_id),
            content_digest=self._prompt_registry.digest(prompt_id),
        )


__all__ = [
    "BASE_ORCHESTRATOR_SYSTEM_PROMPT",
    "DEFAULT_ORCHESTRATOR_PROMPTS",
    "FAST_ORCHESTRATOR_SYSTEM_PROMPT",
    "ULTIMATE_ORCHESTRATOR_SYSTEM_PROMPT",
    "OrchestratorProfileResolutionError",
    "OrchestratorProfileResolver",
    "PromptAssetRegistry",
    "ProfileId",
]
