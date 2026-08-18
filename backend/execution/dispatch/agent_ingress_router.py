"""Authoritative routing for remote interactive agent ingress.

Raw interactive prompts and metadata cross this boundary only as private
``AgentInputObservation`` evidence.  Public projection is permitted solely from
a successful typed conversation decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from common.dto.hitl import A2AInteractionSpec
from execution.dispatch.a2a_interaction import A2AInteractionDisposition
from execution.dispatch.agent_event import AgentInputObservation
from execution.orchestration.run_store import (
    OrchestrationRunStore,
    OrchestrationStoreConflict,
)
from models.orchestration import AgentInputObservationRecord
from models.room import RoomAgentMessage

UNSUPPORTED_INTERACTION_CODE = "unsupported_interaction"
UNSUPPORTED_INTERACTION_MESSAGE = "The agent requested an unsupported interaction."
SUPERVISOR_BLOCKER_SUMMARY = "Agent requested additional input."


class AgentIngressRoute(StrEnum):
    SUPERVISOR_OBSERVATION = "supervisor_observation"
    CONVERSATION_TYPED = "conversation_typed"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class AgentIngressDecision:
    route: AgentIngressRoute
    message_id: str
    room_id: str
    agent_id: str
    task_id: str
    context_id: str
    observed_state: str
    interaction_spec: A2AInteractionSpec | None = None
    orchestration_run_id: str | None = None
    error_code: str | None = None
    public_error: str | None = None

    @property
    def is_typed_conversation(self) -> bool:
        return self.route == AgentIngressRoute.CONVERSATION_TYPED


class AgentIngressMessageReader(Protocol):
    async def get_room_agent_message_by_message_id(
        self, message_id: str
    ) -> RoomAgentMessage | None: ...


class AgentIngressRouter:
    """Resolve persisted ownership and durably classify interactive ingress."""

    def __init__(
        self,
        *,
        message_reader: AgentIngressMessageReader,
        orchestration_run_store: OrchestrationRunStore | None,
        cas_attempts: int = 5,
    ) -> None:
        self._message_reader = message_reader
        self._run_store = orchestration_run_store
        self._cas_attempts = cas_attempts

    async def decide(
        self,
        *,
        message_id: str,
        room_id: str,
        agent_id: str,
        observation: AgentInputObservation | None,
    ) -> AgentIngressDecision:
        if observation is None:
            return self._unsupported(
                message_id=message_id,
                room_id=room_id,
                agent_id=agent_id,
                task_id="missing-authoritative-task",
                context_id="missing-authoritative-context",
                observed_state="input-required",
            )

        message = await self._message_reader.get_room_agent_message_by_message_id(
            message_id
        )
        if not self._message_matches(
            message,
            message_id=message_id,
            room_id=room_id,
            agent_id=agent_id,
        ):
            return self._unsupported_from_observation(
                message_id, room_id, agent_id, observation
            )

        run_id = message.run_id
        if run_id is not None:
            if not isinstance(run_id, str) or not run_id.strip():
                return self._unsupported_from_observation(
                    message_id, room_id, agent_id, observation
                )
            run = (
                await self._run_store.get_run(run_id)
                if self._run_store is not None
                else None
            )
            if run is None or run.room_id != room_id:
                return self._unsupported_from_observation(
                    message_id, room_id, agent_id, observation
                )
            matching_intent = next(
                (
                    intent
                    for intent in run.dispatch_intents
                    if intent.planned_agent_message_id == message_id
                    and intent.agent_id == agent_id
                ),
                None,
            )
            if matching_intent is None:
                return self._unsupported_from_observation(
                    message_id, room_id, agent_id, observation
                )
            await self._append_private_observation(
                run_id=run_id,
                message_id=message_id,
                agent_id=agent_id,
                observation=observation,
            )
            return AgentIngressDecision(
                route=AgentIngressRoute.SUPERVISOR_OBSERVATION,
                message_id=message_id,
                room_id=room_id,
                agent_id=agent_id,
                task_id=observation.task_id,
                context_id=observation.context_id,
                observed_state=observation.observed_state,
                orchestration_run_id=run_id,
            )

        if observation.parser_disposition == A2AInteractionDisposition.TYPED:
            return AgentIngressDecision(
                route=AgentIngressRoute.CONVERSATION_TYPED,
                message_id=message_id,
                room_id=room_id,
                agent_id=agent_id,
                task_id=observation.task_id,
                context_id=observation.context_id,
                observed_state=observation.observed_state,
                interaction_spec=observation.interaction_spec,
            )
        return self._unsupported_from_observation(
            message_id, room_id, agent_id, observation
        )

    async def _append_private_observation(
        self,
        *,
        run_id: str,
        message_id: str,
        agent_id: str,
        observation: AgentInputObservation,
    ) -> None:
        if self._run_store is None:
            raise RuntimeError("orchestration run store is required")
        record = AgentInputObservationRecord(
            classification=observation.parser_disposition.value,
            raw_prompt=observation.raw_prompt,
            raw_metadata=_thaw(observation.interaction_metadata),
            interaction_spec=observation.interaction_spec,
            parser_error=observation.parser_error,
            observed_state=observation.observed_state,
            authoritative_task_id=observation.task_id,
            authoritative_context_id=observation.context_id,
            agent_id=agent_id,
            agent_message_id=message_id,
        )
        for _attempt in range(self._cas_attempts):
            current = await self._run_store.get_run(run_id)
            if current is None:
                raise RuntimeError(f"orchestration run {run_id!r} disappeared")
            if any(
                item.observation_id == record.observation_id
                for item in current.private_agent_input_observations
            ):
                return
            updated = current.model_copy(deep=True)
            updated.private_agent_input_observations.append(record)
            updated.state_version = current.state_version + 1
            try:
                await self._run_store.save_state(
                    updated, expected_version=current.state_version
                )
                return
            except OrchestrationStoreConflict:
                continue
        raise OrchestrationStoreConflict(
            f"failed to append private observation for run_id {run_id!r}"
        )

    @staticmethod
    def _message_matches(
        message: RoomAgentMessage | None,
        *,
        message_id: str,
        room_id: str,
        agent_id: str,
    ) -> bool:
        return bool(
            message is not None
            and message.message_id == message_id
            and message.room_id == room_id
            and message.agent_id == agent_id
        )

    @staticmethod
    def _unsupported_from_observation(
        message_id: str,
        room_id: str,
        agent_id: str,
        observation: AgentInputObservation,
    ) -> AgentIngressDecision:
        return AgentIngressRouter._unsupported(
            message_id=message_id,
            room_id=room_id,
            agent_id=agent_id,
            task_id=observation.task_id,
            context_id=observation.context_id,
            observed_state=observation.observed_state,
        )

    @staticmethod
    def _unsupported(
        *,
        message_id: str,
        room_id: str,
        agent_id: str,
        task_id: str,
        context_id: str,
        observed_state: str,
    ) -> AgentIngressDecision:
        return AgentIngressDecision(
            route=AgentIngressRoute.UNSUPPORTED,
            message_id=message_id,
            room_id=room_id,
            agent_id=agent_id,
            task_id=task_id,
            context_id=context_id,
            observed_state=observed_state,
            error_code=UNSUPPORTED_INTERACTION_CODE,
            public_error=UNSUPPORTED_INTERACTION_MESSAGE,
        )


def _thaw(value: Any) -> Any:
    if hasattr(value, "items"):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple | list | set | frozenset):
        return [_thaw(item) for item in value]
    return value


__all__ = [
    "AgentIngressDecision",
    "AgentIngressRoute",
    "AgentIngressRouter",
    "SUPERVISOR_BLOCKER_SUMMARY",
    "UNSUPPORTED_INTERACTION_CODE",
    "UNSUPPORTED_INTERACTION_MESSAGE",
]
