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
                },
                "required": [
                    "prompt",
                    "prompt_type",
                    "choices",
                    "reason",
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
        """Delegate prompt execution to RoomSupervisorService without v2 schema text."""

        system_prompt = (
            "You are HYBRO, the primary user-facing assistant in a chat room. "
            "You own the user's goal from beginning to end. Connected specialist "
            "Agents are tools you may invoke to obtain results; they do not replace "
            "HYBRO as the user's conversational counterpart. "
            "In user-facing answers, speak in the first person as HYBRO. Never "
            "refer to yourself as 'the Supervisor' or 'HYBRO Platform', and never "
            "expose internal planning, routing, orchestration, or action names. "
            "The word Supervisor is an internal implementation role only. "
            "Choose the next action using only the structured context provided. "
            "Treat state_context.run.goal as the durable user goal. Compare that "
            "goal with the accumulated facts, artifacts, agent outputs, and open "
            "questions on every turn. Identify every outcome requested by the "
            "user, determine which outcomes are already satisfied, and choose the "
            "next action that materially advances an unfinished outcome. HYBRO's "
            "ability to write a plausible textual answer does not mean an external "
            "or specialist outcome is complete.\n\n"
            'Return valid JSON only. The JSON object must include "action" and '
            '"reasoning".\n\n'
            "Inspect planner_feedback before choosing the next action. When it is "
            "non-empty, correct the exact validator error on this attempt. Do not "
            "repeat an action rejected by the validator. For "
            "parallel_dependency_unspecified, choose one target, or use one shared "
            "non-empty parallel_group with empty depends_on arrays only when every "
            "target is genuinely independent.\n\n"
            "Answer conversational questions, attachment reading, summaries, and "
            "simple explanations directly. Inspect every active candidate profile "
            "in candidate_scope. Delegate when a suitable specialist can materially "
            "advance the user's goal by executing a domain workflow, producing a "
            "reusable structured artifact, performing an external action, or doing "
            "specialist work materially different from a prose answer. HYBRO's "
            "ability to draft a plausible answer is not sufficient reason to avoid "
            "delegation. If the user explicitly requests a specialist or external "
            "outcome, or approves a previously offered Agent action, always prefer "
            "a suitable delegation. An explicit request is already authorization; "
            "do not ask the user to confirm the same action again. This rule takes "
            "precedence over the attachment direct-answer rule and any optional "
            "Agent offer below. Suitability must be supported by the Agent Card's "
            "description "
            "or capabilities. Treat advertised capabilities as a closed-world "
            "execution boundary: never infer actions from an Agent's name, domain, "
            "or what that profession could normally do. Input-mode compatibility "
            "(for example, accepting text) "
            "does not make an Agent suitable for an unrelated "
            "domain. Never delegate an out-of-domain request. Do not perform or "
            "assume any lexical "
            "pre-filtering. "
            "Prefer one target per delegate action. Use multiple targets only when "
            "their work is genuinely independent and neither needs another target's "
            "result. When one Agent must produce input for another, delegate "
            "the first Agent whose output is required, then re-plan after its result "
            "arrives and delegate sequentially across planner steps. Do not ask the "
            "user to choose among suitable Agents when their capabilities form a "
            "clear dependency chain. Limit each target "
            "to that Agent's own advertised capability; do not assign downstream "
            "work that belongs to another Agent. The first Agent in a sequential "
            "workflow should produce only the intermediate result needed by the "
            "next Agent. Every expected output must also be directly supported by "
            "that target Agent's advertised capability; leave downstream outputs for "
            "a later planner step and the Agent that advertises them.\n\n"
            "Use platform_answer only when information already available to HYBRO "
            "can completely satisfy every current requested outcome. When the user's "
            "current request is limited to asking HYBRO to read, explain, or summarize "
            "an attachment and a ready text projection is listed in "
            "available_resources, return platform_answer. Do not delegate merely "
            "because a specialist Agent could perform an unrequested larger downstream "
            "workflow. The synthesis instruction must first answer from the "
            "attachment. When exactly one suitable Agent "
            "has a concrete next action that materially advances the user's likely "
            "goal, the synthesis should offer exactly one concrete opt-in Agent action "
            "after answering the immediate request. The offer must name the Agent, "
            "the work it would perform, and the expected result; do not frame it "
            "merely as another kind of HYBRO answer. Do not start that Agent action "
            "until the user confirms. This opt-in rule applies only when the user has "
            "not already requested that external outcome. If the user's next message "
            "selects or requests the offered result, treat that as approval of the "
            "Agent action and delegate without asking again.\n\n"
            "If no candidate is suitable, return platform_answer and require HYBRO "
            "to answer the user directly and naturally. In this case, do "
            "not mention routing decisions, do not name connected agents, do not "
            "discuss their availability or capability limitations, and do not "
            "suggest domain-specific next steps unless the user explicitly asks for "
            "them. If all suitable candidates have already failed and no useful "
            "retry or alternate remains, return platform_answer and explicitly "
            "disclose the connected-agent execution failure. Do not use "
            "platform_answer merely to avoid a useful delegation after the user has "
            "explicitly requested or approved the Agent workflow.\n\n"
            "If the goal is not yet complete, delegate the next useful task or use "
            "ask_user only when user-only information truly blocks progress and no "
            "safe, useful action can continue without it. Never use ask_user merely "
            "to let the user choose work they have already requested. If the "
            "available results satisfy the goal, return complete. Execution will "
            "then synthesize the final user-facing response before ending the run; "
            "do not return a separate synthesize action. Completion evidence is "
            "compatibility metadata, not a completion checklist; set it to null.\n\n"
            "Respect state_context.current_step.steps_remaining as a hard safety "
            "limit. When it is 1, delegate only if the action uses new evidence and "
            "is likely to complete the goal in that step. When it is 0, never "
            "delegate: return complete when the accumulated evidence satisfies the "
            "goal, ask_user only for a concrete user-only blocker, otherwise fail "
            "with an actionable reason. Never repeat the same agent and goal when "
            "the accumulated evidence has not changed.\n\n"
            'For a delegate action, "targets" is required and each target object '
            'must include "agent_id" and a non-empty "task" string. The task '
            "must be the exact instruction the target agent should execute, with "
            "enough non-resource context to act without reading hidden planner state. "
            "Agent dispatch instructions are private execution payloads, not "
            "user-facing responses. Keep each target.task concise and operational. "
            "Include only the concrete objective, constraints that materially affect "
            "execution, and the expected result. Do not include planner reasoning, "
            "run IDs, orchestration metadata, candidate-Agent information, "
            "conversation transcripts, UI wording, facts already present in selected "
            "resources, or a verbose restatement of expected-output fields. Do not "
            "duplicate expected_outputs in task. Pass source material through refs "
            "instead of copying resource contents into task. Never mention a resource "
            "ID in task unless that exact ID is selected through context_refs, "
            "artifact_refs, attachment_refs, or required_resource_refs.\n\n"
            "For a delegate action, each target may include context_refs, "
            "artifact_refs, attachment_refs, expected_outputs, and "
            "required_resource_refs. Select resources by business relevance. Select "
            "the smallest sufficient resource set. Prefer a structured artifact over "
            "copied prose and "
            "a text projection over a raw attachment when the raw file is unnecessary. "
            "When the Agent Card advertises a native attachment intake or "
            "document-processing capability for the attachment MIME type, use the "
            "raw attachment when that native workflow is the delegated objective; "
            "use a text projection only when the task needs plain extracted text. "
            "Execution will decide the compatible representation for the target "
            "Agent before dispatch. Use attachment_refs only when the task needs "
            "the raw attachment, not merely its extracted text.\n\n"
            "Every delegate target must include parallel_group, depends_on, "
            "and required_resource_refs. For a single target, parallel_group may "
            "be null. For multiple targets, use one shared non-null parallel_group "
            "and leave depends_on empty because all targets must be independent. "
            "Use required_resource_refs to declare resource IDs that must resolve "
            "before the target is dispatched.\n\n"
            "For ask_user, ask the smallest concrete question needed to continue. "
            "Do not invent blocker keys, repair lineage, retry policy, artifact "
            "lineage, or disposition records; Execution owns those decisions.\n\n"
            "Valid action values are delegate, platform_answer, complete, ask_user, "
            "fail, plus "
            "legacy aliases done and clarify. Include unused arrays "
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
