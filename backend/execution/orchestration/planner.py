"""Planner adapter boundary for orchestration."""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

from execution.orchestration.action_validator import PlannerActionValidator
from execution.orchestration.context_builder import OrchestrationPlannerContext
from execution.orchestration.planner_prompt import (
    PLANNER_SYSTEM_PROMPT,
    planner_action_schema,
)
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
                "platform_answer",
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
                        "maxItems": 1,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "output_key": {"type": ["string", "null"]},
                                "kind": {"type": "string"},
                                "required": {"type": "boolean", "const": True},
                                "description": {"type": ["string", "null"]},
                                "artifact_name": {"type": "null"},
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
                        "satisfied_output_keys": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "waived_outputs": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "output_key": {"type": "string"},
                                    "reason": {"type": "string", "pattern": "\\S"},
                                    "blocker_keys": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                },
                                "required": [
                                    "output_key",
                                    "reason",
                                    "blocker_keys",
                                ],
                            },
                        },
                        "abandoned_goal_disposition_event_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "requested_goal_family_dispositions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "event_id": {"type": "string", "pattern": "\\S"},
                                    "goal_family_fingerprint": {
                                        "type": "string",
                                        "pattern": "\\S",
                                    },
                                    "through_goal_revision_fingerprint": {
                                        "type": "string",
                                        "pattern": "\\S",
                                    },
                                    "status": {
                                        "type": "string",
                                        "enum": ["abandoned", "superseded"],
                                    },
                                    "reason": {"type": "string", "pattern": "\\S"},
                                    "replacement_goal_family_fingerprint": {
                                        "anyOf": [
                                            {"type": "string", "pattern": "\\S"},
                                            {"type": "null"},
                                        ]
                                    },
                                },
                                "required": [
                                    "event_id",
                                    "goal_family_fingerprint",
                                    "through_goal_revision_fingerprint",
                                    "status",
                                    "reason",
                                    "replacement_goal_family_fingerprint",
                                ],
                            },
                        },
                    },
                    "required": [
                        "satisfied_criteria",
                        "referenced_fact_ids",
                        "referenced_artifact_keys",
                        "unresolved_questions",
                        "final_answer_intent",
                        "confidence",
                        "satisfied_output_keys",
                        "waived_outputs",
                        "abandoned_goal_disposition_event_ids",
                        "requested_goal_family_dispositions",
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
    """Adapt existing supervisor JSON decisions to validated orchestration planner actions."""

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
        return bool(context.state_context.agent_outputs or context.state_context.facts)

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
        """Delegate prompt execution to RoomSupervisorService without rollout schema text."""

        system_prompt = PLANNER_SYSTEM_PROMPT
        user_prompt = json.dumps(
            context.prompt_payload(),
            ensure_ascii=False,
            sort_keys=True,
        )
        return await self._supervisor_service.call_planner_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=planner_action_schema(
                PLANNER_ACTION_RESPONSE_SCHEMA,
                context.candidate_agent_ids,
            ),
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
