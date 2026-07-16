"""Planner adapter boundary for v2 orchestration."""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

from execution.orchestration.action_validator import PlannerActionValidator
from execution.orchestration.context_builder import OrchestrationPlannerContext
from execution.orchestration.room_supervisor_service import RoomSupervisorService
from models.orchestration import PlannerAction

RawPlannerActionProvider = Callable[
    [OrchestrationPlannerContext],
    Mapping[str, Any] | PlannerAction | Awaitable[Mapping[str, Any] | PlannerAction],
]


class OrchestrationPlanner(Protocol):
    """Planner adapter interface for dynamic orchestration run state."""

    async def plan(self, context: OrchestrationPlannerContext) -> PlannerAction: ...


class RoomSupervisorPlannerAdapter:
    """Adapt existing supervisor JSON decisions to validated v2 planner actions."""

    def __init__(
        self,
        *,
        supervisor_service: RoomSupervisorService | None = None,
        raw_action_provider: RawPlannerActionProvider | None = None,
    ) -> None:
        self._supervisor_service = supervisor_service or RoomSupervisorService()
        self._raw_action_provider = raw_action_provider

    async def plan(self, context: OrchestrationPlannerContext) -> PlannerAction:
        raw_action = await self._raw_action(context)
        action = self._parse_action(raw_action)
        return PlannerActionValidator.validate(
            action,
            candidate_agent_ids=context.candidate_agent_ids,
            steps_used=context.state_context.current_step.steps_used,
            step_budget=context.state_context.current_step.step_budget,
            has_agent_output=bool(context.state_context.agent_outputs),
        )

    async def _raw_action(
        self,
        context: OrchestrationPlannerContext,
    ) -> Mapping[str, Any] | PlannerAction:
        if self._raw_action_provider is not None:
            result = self._raw_action_provider(context)
            if inspect.isawaitable(result):
                result = await result
            return result

        return await self._call_supervisor_service(context)

    async def _call_supervisor_service(
        self,
        context: OrchestrationPlannerContext,
    ) -> Mapping[str, Any]:
        """Delegate prompt execution to RoomSupervisorService without v2 schema text."""

        system_prompt = (
            "You are a Supervisor coordinating specialist agents in a chat room. "
            "Choose the next action using only the structured context provided."
        )
        user_prompt = json.dumps(
            context.prompt_payload(),
            ensure_ascii=False,
            sort_keys=True,
        )
        return await self._supervisor_service.call_planner_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

    def _parse_action(
        self,
        raw_action: Mapping[str, Any] | PlannerAction,
    ) -> PlannerAction:
        if isinstance(raw_action, PlannerAction):
            return raw_action
        if not isinstance(raw_action, Mapping):
            raise ValueError("planner adapter expected a JSON object")
        return self._supervisor_service.parse_planner_action(dict(raw_action))
