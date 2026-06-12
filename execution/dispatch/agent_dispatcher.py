"""AgentDispatcher — agent assignment and resolution for room messages.

Owns the logic for resolving which agent should handle a given
``RoomAgentMessage``: allowed-ID resolution (including group expansion),
user-input extraction, delegation to ``AgentResolverService``, and
persistence of the assignment.

Extracted from ``RoomMessageCenter`` as part of the A-4 decomposition.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from common.utils.logger import get_logger
from execution.state.task_state_manager import get_task
from models.agent import Agent
from models.dispatcher import AssignResult
from models.room import RoomAgentMessage

if TYPE_CHECKING:
    from app_shell.agent_resolver_service import AgentResolverService

    class DispatchMessageWriter(Protocol):
        async def update_room_agent_message_by_message_id(
            self, message_id: str, room_agent_message
        ) -> bool: ...

    class DispatchAgentLookup(Protocol):
        async def get_agent_by_agent_id(self, agent_id: str): ...

    class DispatchAgentGroupReader(Protocol):
        async def get_agent_group_by_id(self, group_id: str): ...

logger = get_logger(__name__)


# ------------------------------------------------------------------
# AgentDispatcher
# ------------------------------------------------------------------


class AgentDispatcher:
    """Resolves and assigns agents to room messages.

    Dependencies are injected via the constructor so the dispatcher
    can be tested in isolation.
    """

    def __init__(
        self,
        *,
        agent_resolver: AgentResolverService,
        message_writer: DispatchMessageWriter,
        agent_lookup: DispatchAgentLookup,
        agent_group_reader: DispatchAgentGroupReader,
    ) -> None:
        self.agent_resolver = agent_resolver
        self._message_writer = message_writer
        self._agent_lookup = agent_lookup
        self._agent_group_reader = agent_group_reader

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def assign_agent(
        self, current_message: RoomAgentMessage
    ) -> AssignResult:
        """Assign an agent to the message by inferring from content.

        Uses the ``AgentResolverService`` to find the best accessible agent
        from the allowed agent list.  Returns an ``AssignResult`` with the
        chosen agent or a human-readable ``failure_reason`` when no agent is
        available.
        """
        allowed_agent_ids = await self._resolve_allowed_agent_ids(current_message)
        user_input = self._extract_user_input(current_message)

        if not user_input:
            logger.error(
                "AgentDispatcher: No user input in message %s, cannot infer agent",
                current_message.message_id,
            )
            return AssignResult(
                agent=None,
                failure_reason="Unable to determine what to ask an agent — the message appears to be empty.",
            )

        logger.info(
            "AgentDispatcher: Inferring agent for message %s (input length: %d, scoped_ids=%d)",
            current_message.message_id,
            len(user_input),
            len(allowed_agent_ids),
        )

        result = await self.agent_resolver.resolve(
            user_input,
            allowed_agent_ids=allowed_agent_ids if allowed_agent_ids else None,
            user_id=current_message.user_id,
        )

        if result.agent is None:
            logger.error(
                "AgentDispatcher: No accessible agent for message %s: %s",
                current_message.message_id,
                result.failure_reason,
            )
            return AssignResult(agent=None, failure_reason=result.failure_reason)

        agent = result.agent
        current_message.agent_id = agent.agent_id

        update_success = (
            await self._message_writer.update_room_agent_message_by_message_id(
                message_id=current_message.message_id,
                room_agent_message=current_message,
            )
        )

        if not update_success:
            logger.error(
                "AgentDispatcher: Failed to update agent assignment for message %s",
                current_message.message_id,
            )
            return AssignResult(
                agent=None,
                failure_reason="Internal error: failed to persist agent assignment.",
            )

        logger.info(
            "AgentDispatcher: Assigned agent %s to message %s",
            agent.agent_id,
            current_message.message_id,
        )
        return AssignResult(agent=agent)

    async def assign_agent_for_queue(
        self, current_message: RoomAgentMessage
    ) -> tuple[Agent | None, str | None]:
        """Convenience adapter returning ``(Agent | None, failure_reason)``.

        This is the shape expected by ``QueueExecutor._resolve_agent_for_message``.
        """
        result = await self.assign_agent(current_message)
        return result.agent, result.failure_reason

    async def resolve_agent(self, agent_id: str, room_id: str) -> Agent | None:
        """Resolve an agent by ID. Returns ``None`` if not found or inactive.

        Unlike ``assign_agent_for_queue``, this does NOT attempt re-assignment.
        Used by ``SupervisorExecutor`` where the supervisor handles failures
        via the next ``decide_next`` iteration.
        """
        from models.agent import AgentStatus

        agent = await self._agent_lookup.get_agent_by_agent_id(agent_id)
        if agent is None:
            return None
        if agent.agent_status != AgentStatus.active:
            logger.warning(
                "AgentDispatcher: Agent %s is %s, returning None",
                agent_id,
                agent.agent_status,
            )
            return None
        return agent

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _resolve_allowed_agent_ids(
        self,
        current_message: RoomAgentMessage,
    ) -> list[str]:
        """Resolve allowed agent IDs from extend_info, merging group members."""
        if not isinstance(current_message.extend_info, dict):
            return []

        allowed_agent_ids = current_message.extend_info.get("allowed_agent_ids") or []
        target_group = current_message.extend_info.get("target_group")

        # Normalize target_group into a list
        target_groups: list[str] = []
        if isinstance(target_group, list | tuple):
            target_groups = [str(g) for g in target_group]
        elif isinstance(target_group, str) and target_group:
            target_groups = [target_group]

        merged_ids = set(str(aid) for aid in allowed_agent_ids)
        for tg in target_groups:
            if tg in ["all_agents", "room_team"]:
                continue
            try:
                group = await self._agent_group_reader.get_agent_group_by_id(tg)
                if group and group.agents:
                    merged_ids |= set(str(aid) for aid in group.agents)
            except Exception as e:
                logger.error(
                    "AgentDispatcher: Failed to load agents for group %s: %s", tg, e
                )

        return list(merged_ids)

    @staticmethod
    def _extract_user_input(current_message: RoomAgentMessage) -> str:
        """Extract the user's text input from the message's first history entry."""
        try:
            task = get_task(current_message)
            if (
                task
                and task.history
                and len(task.history) > 0
                and task.history[0].parts
                and len(task.history[0].parts) > 0
            ):
                first_part = task.history[0].parts[0]
                if first_part.root and hasattr(first_part.root, "text"):
                    return first_part.root.text or ""
        except (IndexError, AttributeError) as e:
            logger.warning(
                "AgentDispatcher: Failed to extract content from message %s: %s",
                current_message.message_id,
                e,
            )
        return ""
