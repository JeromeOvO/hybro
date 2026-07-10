"""Planner adapter boundary for v2 orchestration."""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

from execution.orchestration.action_validator import PlannerActionValidator
from execution.orchestration.context_builder import OrchestrationPlannerContext
from execution.orchestration.room_supervisor_service import RoomSupervisorService
from models.orchestration import PlannerAction, PlannerActionType

RawPlannerActionProvider = Callable[
    [OrchestrationPlannerContext],
    Mapping[str, Any] | PlannerAction | Awaitable[Mapping[str, Any] | PlannerAction],
]

PLANNER_ACTION_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "action": {
            "type": "string",
            "enum": [
                "delegate",
                "synthesize",
                "complete",
                "ask_user",
                "fail",
                "done",
                "clarify",
            ],
        },
        "reasoning": {"type": "string"},
        "targets": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "agent_id": {"type": "string"},
                    "agent_name": {"type": ["string", "null"]},
                    "task": {"type": "string"},
                    "parallel_group": {"type": ["string", "null"]},
                    "depends_on": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "required_resource_refs": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "context_refs": {
                        "type": "array",
                        "items": {"$ref": "#/$defs/dispatch_ref"},
                    },
                    "artifact_refs": {
                        "type": "array",
                        "items": {"$ref": "#/$defs/dispatch_ref"},
                    },
                    "attachment_refs": {
                        "type": "array",
                        "items": {"$ref": "#/$defs/dispatch_ref"},
                    },
                    "expected_outputs": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "output_key": {"type": ["string", "null"]},
                                "kind": {"type": "string"},
                                "required": {"type": "boolean"},
                                "description": {"type": ["string", "null"]},
                                "artifact_name": {"type": ["string", "null"]},
                                "required_fields": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "allow_partial": {"type": "boolean"},
                            },
                            "required": [
                                "output_key",
                                "kind",
                                "required",
                                "description",
                                "artifact_name",
                                "required_fields",
                                "allow_partial",
                            ],
                        },
                    },
                    "repair_of_intent_id": {"type": ["string", "null"]},
                },
                "required": [
                    "agent_id",
                    "agent_name",
                    "task",
                    "parallel_group",
                    "depends_on",
                    "required_resource_refs",
                    "context_refs",
                    "artifact_refs",
                    "attachment_refs",
                    "expected_outputs",
                    "repair_of_intent_id",
                ],
            },
        },
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "prompt": {"type": "string"},
                    "prompt_type": {
                        "type": "string",
                        "enum": ["text", "choice", "confirmation"],
                    },
                    "choices": {
                        "type": ["array", "null"],
                        "items": {"type": "string"},
                    },
                    "reason": {
                        "type": "string",
                        "enum": ["initial_clarification", "blocker"],
                    },
                    "blocker_keys": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": [
                    "prompt",
                    "prompt_type",
                    "choices",
                    "reason",
                    "blocker_keys",
                ],
            },
        },
        "synthesis_instruction": {"type": ["string", "null"]},
        "failure_reason": {"type": ["string", "null"]},
        "completion_evidence": {
            "anyOf": [
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "satisfied_criteria": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "referenced_fact_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "referenced_artifact_keys": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "unresolved_questions": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "final_answer_intent": {"type": "string"},
                        "confidence": {"type": "number"},
                    },
                    "required": [
                        "satisfied_criteria",
                        "referenced_fact_ids",
                        "referenced_artifact_keys",
                        "unresolved_questions",
                        "final_answer_intent",
                        "confidence",
                    ],
                },
                {"type": "null"},
            ]
        },
    },
    "required": [
        "action",
        "reasoning",
        "targets",
        "questions",
        "synthesis_instruction",
        "failure_reason",
        "completion_evidence",
    ],
    "$defs": {
        "dispatch_ref": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["context", "artifact", "attachment"],
                },
                "ref_id": {"type": "string"},
                "source_agent_message_id": {"type": ["string", "null"]},
                "mime_type": {"type": ["string", "null"]},
                "required": {"type": "boolean"},
            },
            "required": [
                "kind",
                "ref_id",
                "source_agent_message_id",
                "mime_type",
                "required",
            ],
        },
    },
}


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
        has_agent_output = bool(context.state_context.agent_outputs)
        if action.action == PlannerActionType.COMPLETE:
            has_agent_output = self._has_completion_basis(context)
        return PlannerActionValidator.validate(
            action,
            candidate_agent_ids=context.candidate_agent_ids,
            steps_used=context.state_context.current_step.steps_used,
            step_budget=context.state_context.current_step.step_budget,
            has_agent_output=has_agent_output,
        )

    @staticmethod
    def _has_completion_basis(context: OrchestrationPlannerContext) -> bool:
        return bool(
            context.state_context.agent_outputs or context.state_context.facts
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
            "Choose the next action using only the structured context provided.\n\n"
            "Return valid JSON only. The JSON object must include \"action\" and "
            "\"reasoning\".\n\n"
            "For a delegate action, \"targets\" is required and each target object "
            "must include \"agent_id\" and a non-empty \"task\" string. The task "
            "must be the exact instruction the target agent should execute, with "
            "enough context to act without reading hidden planner state.\n\n"
            "For a delegate action, each target may include context_refs, "
            "artifact_refs, attachment_refs, and expected_outputs. Select "
            "context_refs for ready text projections and facts, artifact_refs "
            "for upstream agent artifacts. Only include attachment_refs when "
            "the target agent's candidate_scope input_modes support that "
            "attachment MIME. Prefer artifact_refs over raw attachment_refs "
            "when an upstream agent has produced a structured artifact. Do not include "
            "attachment_policy; supervisor dispatch always uses "
            "explicit_refs_only. expected_outputs must be an array of objects "
            "with kind, required, and description; do not use plain strings.\n\n"
            "Every delegate target must include parallel_group, depends_on, "
            "and required_resource_refs. For a single target, parallel_group may "
            "be null. For multiple targets, use one shared non-null parallel_group "
            "and leave depends_on empty because all targets must be independent. "
            "Use required_resource_refs to declare resource IDs that must resolve "
            "before the target is dispatched.\n\n"
            "Unknown values are non-blocking by default. Continue with explicit assumptions\n"
            "and conditional results when useful. Use repair_of_intent_id only for semantic\n"
            "partial/no-progress repair. Failed operational retries use open failure recovery\n"
            "lineage and leave repair_of_intent_id null. Post-dispatch ask_user questions must\n"
            "reference validated blocker_keys. Do not delegate the same output contract to the\n"
            "same agent twice in one action.\n\n"
            "Valid action values are delegate, synthesize, complete, ask_user, "
            "fail, plus legacy aliases done and clarify. Include unused arrays "
            "as [] and unused nullable fields as null."
        )
        user_prompt = json.dumps(
            context.prompt_payload(),
            ensure_ascii=False,
            sort_keys=True,
        )
        return await self._supervisor_service.call_planner_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=PLANNER_ACTION_RESPONSE_SCHEMA,
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
