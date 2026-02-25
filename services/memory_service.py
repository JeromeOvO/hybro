from uuid import uuid4

from common.utils.context_utils import (
    add_turn_to_history,
    build_context_for_agent,
    clean_mention_format,
    get_context_stats,
    migrate_legacy_memory,
)
from common.utils.logger import get_logger
from common.utils.time import utcnow
from models.error import SessionIdRequiredError
from models.memory import ChatContext, ContextData, MemoryContent, RoomMemory
from models.request import ChatMemoryRequest, RoomCenterMemoryRequest
from models.response import ChatMemoryResponse, RoomCenterMemoryResponse
from services.database_service import db_service
from services.openai_service import openai_service

logger = get_logger(__name__)


# Chat Memory Service Manager
class ChatMemoryService:
    def __init__(self):
        self.database_service = db_service  # Use singleton
        self.openai_service = openai_service  # Use singleton

    # Chat Contexts
    async def create_chat_context(
        self, request: ChatMemoryRequest
    ) -> ChatMemoryResponse:
        """
        Create a chat context in the database
        """

        try:
            new_chat_context = ChatContext(
                memory_id=str(uuid4()),  # Generate a unique memory_id
                user_name=request.user_name,
                session_id=request.session_id,
                context_data=ContextData(
                    context_content=request.user_input
                    if request.user_input is not None
                    else ""
                ),
                created_at=utcnow(),
                updated_at=utcnow(),
                extend_info=[],
            )
            success = await self.database_service.add_chat_context(new_chat_context)
            if success:
                return ChatMemoryResponse(
                    user_name=request.user_name,
                    chat_context=new_chat_context,
                    success=True,
                    error=None,
                    status_code=200,
                )
            else:
                return ChatMemoryResponse(
                    user_name=request.user_name,
                    success=False,
                    error="Failed to add chat context",
                    status_code=500,
                )
        except Exception as e:
            return ChatMemoryResponse(
                user_name=request.user_name,
                success=False,
                error=str(e),
                status_code=500,
            )

    async def get_chat_context_by_session_id(
        self, request: ChatMemoryRequest
    ) -> ChatMemoryResponse:
        """
        Get a chat context by session_id
        """

        if request.session_id is None:
            raise SessionIdRequiredError()

        try:
            chat_context = await self.database_service.get_chat_context_by_session_id(
                request.session_id
            )
            if chat_context:
                return ChatMemoryResponse(
                    user_name=request.user_name,
                    success=True,
                    error=None,
                    status_code=200,
                    chat_context=chat_context,
                )
            else:
                return ChatMemoryResponse(
                    user_name=request.user_name,
                    success=False,
                    error="Chat context not found",
                    status_code=404,
                )
        except Exception as e:
            return ChatMemoryResponse(
                user_name=request.user_name,
                success=False,
                error=str(e),
                status_code=500,
            )

    async def update_chat_context_by_session_id(
        self, request: ChatMemoryRequest
    ) -> ChatMemoryResponse:
        """
        Update a chat context by session_id
        """

        if request.session_id is None:
            raise SessionIdRequiredError()

        try:
            chat_context = await self.database_service.get_chat_context_by_session_id(
                request.session_id
            )
        except Exception as e:
            return ChatMemoryResponse(
                user_name=request.user_name,
                success=False,
                error=str(e),
                status_code=500,
            )

        new_context_data = await self.openai_service.generate_chat_context(
            request.user_input, request.agent_response, chat_context.context_data
        )

        try:
            chat_context = ChatContext(
                memory_id=chat_context.memory_id,  # Generate a unique memory_id
                user_name=request.user_name,
                session_id=request.session_id,
                context_data=ContextData(context_content=new_context_data),
                created_at=chat_context.created_at,
                updated_at=utcnow(),
                extend_info=chat_context.extend_info,
            )
            success = await self.database_service.update_chat_context_by_session_id(
                request.session_id, chat_context
            )
            if success:
                return ChatMemoryResponse(
                    user_name=request.user_name,
                    success=True,
                    error=None,
                    status_code=200,
                )
            else:
                return ChatMemoryResponse(
                    user_name=request.user_name,
                    success=False,
                    error="Failed to update chat context",
                    status_code=500,
                )
        except Exception as e:
            return ChatMemoryResponse(
                user_name=request.user_name,
                success=False,
                error=str(e),
                status_code=500,
            )

    async def delete_chat_context_by_session_id(
        self, request: ChatMemoryRequest
    ) -> ChatMemoryResponse:
        """
        Delete a chat context by session_id
        """
        try:
            success = await self.database_service.delete_chat_context_by_session_id(
                request.session_id
            )
            if success:
                return ChatMemoryResponse(
                    user_name=request.user_name,
                    success=True,
                    error=None,
                    status_code=200,
                )
            else:
                return ChatMemoryResponse(
                    user_name=request.user_name,
                    success=False,
                    error="Failed to delete chat context",
                    status_code=500,
                )
        except Exception as e:
            return ChatMemoryResponse(
                user_name=request.user_name,
                success=False,
                error=str(e),
                status_code=500,
            )


class RoomMemoryService:
    def __init__(self):
        self.database_service = db_service  # Use singleton
        self.openai_service = openai_service  # Use singleton

    async def create_room_memory(
        self, request: RoomCenterMemoryRequest
    ) -> RoomCenterMemoryResponse:
        """
        Create a room memory in the database with new structured format.
        """
        try:
            # Create new structured MemoryContent
            memory_content = MemoryContent()

            # If there's initial content, add it as first user turn
            if request.memory_content:
                memory_content = add_turn_to_history(
                    memory_content=memory_content,
                    role="user",
                    content=request.memory_content,
                )

            new_room_memory = RoomMemory(
                room_id=request.room_id,
                memory_id=request.memory_id or str(uuid4()),
                memory_content=memory_content,
                memory_created_at=request.memory_created_at or utcnow(),
                extend_info=request.extend_info,
            )
            success = await self.database_service.add_room_memory(new_room_memory)
            if success:
                return RoomCenterMemoryResponse(
                    room_id=request.room_id,
                    memory_id=new_room_memory.memory_id,
                    memory=new_room_memory,
                    success=True,
                    error=None,
                    status_code=200,
                )
            else:
                return RoomCenterMemoryResponse(
                    room_id=request.room_id,
                    memory_id=request.memory_id,
                    memory=None,
                    success=False,
                    error="Failed to create room memory",
                    status_code=500,
                )
        except Exception as e:
            return RoomCenterMemoryResponse(
                room_id=request.room_id,
                memory_id=request.memory_id,
                memory=None,
                success=False,
                error=str(e),
                status_code=500,
            )

    async def get_room_memory_by_room_id(
        self, request: RoomCenterMemoryRequest
    ) -> RoomCenterMemoryResponse:
        """
        Get a room memory by room_id
        """
        try:
            room_memory = await self.database_service.get_room_memory_by_room_id(
                request.room_id
            )
            if room_memory:
                return RoomCenterMemoryResponse(
                    room_id=request.room_id,
                    memory_id=room_memory.memory_id,
                    memory=room_memory,
                    success=True,
                    error=None,
                    status_code=200,
                )
            else:
                return RoomCenterMemoryResponse(
                    room_id=request.room_id,
                    memory_id=None,
                    memory=None,
                    success=False,
                    error="Room memory not found",
                    status_code=404,
                )
        except Exception as e:
            return RoomCenterMemoryResponse(
                room_id=request.room_id,
                memory_id=None,
                memory=None,
                success=False,
                error=str(e),
                status_code=500,
            )

    async def update_room_memory_by_room_id(
        self, request: RoomCenterMemoryRequest
    ) -> RoomCenterMemoryResponse:
        """
        Update a room memory by room_id
        """
        try:
            room_memory_response = (
                await self.database_service.update_room_memory_by_room_id(
                    request.room_id, request.memory
                )
            )
            if room_memory_response:
                return RoomCenterMemoryResponse(
                    room_id=request.room_id,
                    memory_id=request.memory_id,
                    memory=request.memory,
                    success=True,
                    error=None,
                    status_code=200,
                )
            else:
                return RoomCenterMemoryResponse(
                    room_id=request.room_id,
                    memory_id=request.memory_id,
                    memory=None,
                    success=False,
                    error="Room memory not found",
                    status_code=404,
                )
        except Exception as e:
            return RoomCenterMemoryResponse(
                room_id=request.room_id,
                memory_id=request.memory_id,
                memory=None,
                success=False,
                error=str(e),
                status_code=500,
            )

    async def get_room_memory_by_memory_id(
        self, request: RoomCenterMemoryRequest
    ) -> RoomCenterMemoryResponse:
        """
        Get a room memory by memory_id
        """
        try:
            room_memory = await self.database_service.get_room_memory_by_memory_id(
                request.memory_id
            )
            if room_memory:
                return RoomCenterMemoryResponse(
                    room_id=request.room_id,
                    memory_id=room_memory.memory_id,
                    memory=room_memory,
                    success=True,
                    error=None,
                    status_code=200,
                )
            else:
                return RoomCenterMemoryResponse(
                    room_id=request.room_id,
                    memory_id=request.memory_id,
                    memory=None,
                    success=False,
                    error="Room memory not found",
                    status_code=404,
                )
        except Exception as e:
            return RoomCenterMemoryResponse(
                room_id=request.room_id,
                memory_id=request.memory_id,
                memory=None,
                success=False,
                error=str(e),
                status_code=500,
            )

    async def update_room_memory_by_memory_id(
        self, request: RoomCenterMemoryRequest
    ) -> RoomCenterMemoryResponse:
        """
        Update a room memory by memory_id
        """
        try:
            room_memory = await self.database_service.get_room_memory_by_memory_id(
                request.memory_id
            )
            if room_memory:
                return RoomCenterMemoryResponse(
                    room_id=request.room_id,
                    memory_id=room_memory.memory_id,
                    memory=room_memory,
                    success=True,
                    error=None,
                    status_code=200,
                )
            else:
                return RoomCenterMemoryResponse(
                    room_id=request.room_id,
                    memory_id=request.memory_id,
                    memory=None,
                    success=False,
                    error="Room memory not found",
                    status_code=404,
                )
        except Exception as e:
            return RoomCenterMemoryResponse(
                room_id=request.room_id,
                memory_id=request.memory_id,
                memory=None,
                success=False,
                error=str(e),
                status_code=500,
            )

    async def delete_room_memory_by_memory_id(
        self, request: RoomCenterMemoryRequest
    ) -> RoomCenterMemoryResponse:
        """
        Delete a room memory by memory_id
        """
        try:
            success = await self.database_service.delete_room_memory_by_memory_id(
                request.memory_id
            )
            if success:
                return RoomCenterMemoryResponse(
                    room_id=request.room_id,
                    memory_id=request.memory_id,
                    memory=None,
                    success=True,
                    error=None,
                    status_code=200,
                )
            else:
                return RoomCenterMemoryResponse(
                    room_id=request.room_id,
                    memory_id=None,
                    memory=None,
                    success=False,
                    error="Room memory not found",
                    status_code=404,
                )
        except Exception as e:
            return RoomCenterMemoryResponse(
                room_id=request.room_id,
                memory_id=None,
                memory=None,
                success=False,
                error=str(e),
                status_code=500,
            )

    async def initialize_or_update_room_memory(
        self, request: RoomCenterMemoryRequest
    ) -> RoomCenterMemoryResponse:
        """
        Initialize or update room memory with a new user message.
        Uses ChatGPT/Claude-style conversation history management.

        The message is cleaned of @mention UUIDs before storage.
        """
        room_id = request.room_id
        new_message = request.memory_content
        room_agent_set = request.room_agent_set or {}
        user_id = request.user_id

        room_memory = await self.database_service.get_room_memory_by_room_id(room_id)

        if not room_memory:
            # Initialize new room memory with structured format
            memory_content = MemoryContent()

            if new_message:
                # Clean @mentions before storing
                clean_message = clean_mention_format(new_message, room_agent_set)
                memory_content = add_turn_to_history(
                    memory_content=memory_content,
                    role="user",
                    content=clean_message,
                    user_id=user_id,
                )

            room_memory = RoomMemory(
                room_id=room_id,
                memory_id=str(uuid4()),
                memory_content=memory_content,
            )

            add_room_memory_success = await self.database_service.add_room_memory(
                room_memory
            )
            if not add_room_memory_success:
                return RoomCenterMemoryResponse(
                    room_id=room_id,
                    success=False,
                    error="Failed to add room memory",
                    status_code=500,
                )

            logger.info(
                f"RoomMemoryService: Initialized new room memory for room {room_id}"
            )
        else:
            # Migrate legacy memory if needed
            if room_memory.memory_content:
                room_memory.memory_content = migrate_legacy_memory(
                    room_memory.memory_content
                )

            if new_message:
                # Clean @mentions before storing
                clean_message = clean_mention_format(new_message, room_agent_set)

                # Add as user turn to conversation history
                room_memory.memory_content = add_turn_to_history(
                    memory_content=room_memory.memory_content,
                    role="user",
                    content=clean_message,
                    user_id=user_id,
                )

                # Log context stats for debugging
                stats = get_context_stats(room_memory.memory_content)
                logger.debug(
                    f"RoomMemoryService: Room {room_id} context stats: {stats}"
                )

            room_memory_response = (
                await self.database_service.update_room_memory_by_room_id(
                    room_id, room_memory
                )
            )

            if not room_memory_response:
                logger.error("RoomMemoryService: Failed to update room memory")
                return RoomCenterMemoryResponse(
                    room_id=room_id,
                    memory_id=room_memory.memory_id,
                    success=False,
                    error="Failed to update room memory",
                    status_code=500,
                )

        return RoomCenterMemoryResponse(
            room_id=room_id,
            memory_id=room_memory.memory_id,
            memory=room_memory,
            success=True,
            error=None,
            status_code=200,
        )

    async def add_agent_response_to_memory(
        self,
        room_id: str,
        agent_id: str,
        agent_name: str,
        response_text: str,
    ) -> RoomCenterMemoryResponse:
        """
        Add an agent's response to the room conversation history.
        Called after an agent completes its response.

        Args:
            room_id: The room ID
            agent_id: The agent's ID
            agent_name: The agent's display name
            response_text: The agent's response text

        Returns:
            RoomCenterMemoryResponse with success status
        """
        room_memory = await self.database_service.get_room_memory_by_room_id(room_id)

        if not room_memory:
            logger.error(
                f"RoomMemoryService: Room memory not found for room {room_id}"
            )
            return RoomCenterMemoryResponse(
                room_id=room_id,
                success=False,
                error="Room memory not found",
                status_code=404,
            )

        # Migrate legacy memory if needed
        if room_memory.memory_content:
            room_memory.memory_content = migrate_legacy_memory(
                room_memory.memory_content
            )

        # Add agent response to conversation history
        room_memory.memory_content = add_turn_to_history(
            memory_content=room_memory.memory_content,
            role="agent",
            content=response_text,
            agent_id=agent_id,
            agent_name=agent_name,
        )

        # Log context stats
        stats = get_context_stats(room_memory.memory_content)
        logger.debug(
            f"RoomMemoryService: Added agent response to room {room_id}, stats: {stats}"
        )

        update_success = await self.database_service.update_room_memory_by_room_id(
            room_id, room_memory
        )

        if not update_success:
            logger.error(
                f"RoomMemoryService: Failed to update room memory with agent response"
            )
            return RoomCenterMemoryResponse(
                room_id=room_id,
                memory_id=room_memory.memory_id,
                success=False,
                error="Failed to update room memory",
                status_code=500,
            )

        return RoomCenterMemoryResponse(
            room_id=room_id,
            memory_id=room_memory.memory_id,
            memory=room_memory,
            success=True,
            error=None,
            status_code=200,
        )

    async def get_context_for_agent(
        self,
        room_id: str,
        current_task: str,
        agent_name: str | None = None,
    ) -> str:
        """
        Build context string for an agent request (ChatGPT/Claude style).

        Args:
            room_id: The room ID
            current_task: The current user request/task
            agent_name: Name of the agent (for personalization)

        Returns:
            Formatted context string ready to send to agent
        """
        room_memory = await self.database_service.get_room_memory_by_room_id(room_id)

        if not room_memory or not room_memory.memory_content:
            # No history, just return the current task
            if agent_name:
                return (
                    f"[Current request]\nUser: {current_task}\n\n"
                    f"You are {agent_name}. Please respond to the request above."
                )
            return f"[Current request]\nUser: {current_task}"

        # Migrate legacy memory if needed
        room_memory.memory_content = migrate_legacy_memory(room_memory.memory_content)

        return build_context_for_agent(
            memory_content=room_memory.memory_content,
            current_task=current_task,
            agent_name=agent_name,
        )

    async def add_synthesis_to_history(
        self,
        room_id: str,
        synthesis_text: str,
        trajectory: "SupervisorTrajectory | None" = None,
    ) -> str | None:
        """
        Add supervisor synthesis text to room conversation history (§11.3).

        Creates a SUPERVISOR-role turn with the synthesis content, persists it,
        and returns the new turn_id on success (needed by update_room_summary
        to populate RoomSummary.updated_after_turn_id per §4.2).

        When trajectory is provided, agent contributions are extracted into the
        turn for richer turn_notes (forward-compatibility with Phase 4B search).

        Args:
            room_id: The room ID
            synthesis_text: The synthesis text from the supervisor
            trajectory: Optional trajectory for agent contribution extraction

        Returns:
            The new turn_id if successfully persisted, None otherwise
        """
        room_memory = await self.database_service.get_room_memory_by_room_id(room_id)
        if not room_memory:
            logger.error(
                "RoomMemoryService.add_synthesis_to_history: "
                "Room memory not found for room %s", room_id,
            )
            return None

        if room_memory.memory_content:
            room_memory.memory_content = migrate_legacy_memory(
                room_memory.memory_content
            )

        # Enrich synthesis content with trajectory agent contributions
        enriched_content = synthesis_text
        if trajectory and trajectory.entries:
            agent_contributions = []
            for entry in trajectory.entries:
                for result in getattr(entry, "results", []):
                    if result.success and result.agent_name:
                        task_summary = (result.task or "")[:100]
                        agent_contributions.append(
                            f"{result.agent_name}: {task_summary}"
                        )
            if agent_contributions:
                contributions_text = "; ".join(agent_contributions[:5])
                enriched_content = (
                    f"{synthesis_text}\n\n"
                    f"[Agent contributions: {contributions_text}]"
                )

        room_memory.memory_content = add_turn_to_history(
            memory_content=room_memory.memory_content,
            role="supervisor",
            content=enriched_content,
            turn_type="message",
        )

        # Grab the turn_id of the just-appended synthesis turn
        synthesis_turn_id: str | None = None
        if room_memory.memory_content and room_memory.memory_content.conversation_history:
            synthesis_turn_id = room_memory.memory_content.conversation_history[-1].turn_id

        success = await self.database_service.update_room_memory_by_room_id(
            room_id, room_memory
        )
        if not success:
            logger.error(
                "RoomMemoryService.add_synthesis_to_history: "
                "Failed to persist synthesis turn for room %s", room_id,
            )
            return None
        return synthesis_turn_id

    async def update_room_summary(
        self,
        room_id: str,
        synthesis_text: str,
        synthesis_turn_id: str | None = None,
    ) -> bool:
        """
        Update RoomMemory.room_summary using LLM extraction from synthesis text (§9, §11.3).

        Sends the synthesis text to a fast LLM with JSON mode to extract structured
        room summary fields. On any failure, the existing summary is preserved.

        Args:
            room_id: The room ID
            synthesis_text: The synthesis text to extract summary from
            synthesis_turn_id: The turn_id of the synthesis that triggered this update
                (populates RoomSummary.updated_after_turn_id per §4.2)

        Returns:
            True if successfully updated, False if extraction or persistence failed
        """
        room_memory = await self.database_service.get_room_memory_by_room_id(room_id)
        if not room_memory:
            logger.warning(
                "RoomMemoryService.update_room_summary: "
                "Room memory not found for room %s", room_id,
            )
            return False

        extraction_prompt = (
            "Extract structured room summary fields from the following synthesis. "
            "Return ONLY valid JSON with these keys:\n"
            '- "current_goal": string or null — what the user/room is trying to accomplish\n'
            '- "key_decisions": list of strings — decisions that should persist\n'
            '- "open_questions": list of strings — unresolved questions or blockers\n'
            '- "recent_agent_contributions": list of strings — last 3-5 agent result summaries\n'
            '- "important_constraints": list of strings — hard constraints stated\n\n'
            f"Synthesis:\n{synthesis_text}"
        )

        try:
            extracted = await self.openai_service.call_supervisor_llm_json(
                system_prompt="You extract structured information from text. Respond with valid JSON only.",
                user_prompt=extraction_prompt,
                model="gpt-4o-mini",
            )
        except Exception as e:
            logger.warning(
                "RoomMemoryService.update_room_summary: "
                "LLM extraction failed for room %s: %s", room_id, e,
            )
            return False

        from models.memory import RoomSummary

        existing = room_memory.room_summary or RoomSummary()
        extracted_goal = extracted.get("current_goal")
        extracted_decisions = extracted.get("key_decisions")
        extracted_questions = extracted.get("open_questions")
        extracted_contributions = extracted.get("recent_agent_contributions")
        extracted_constraints = extracted.get("important_constraints")

        room_memory.room_summary = RoomSummary(
            current_goal=extracted_goal if extracted_goal is not None else existing.current_goal,
            key_decisions=extracted_decisions if extracted_decisions is not None else existing.key_decisions,
            open_questions=extracted_questions if extracted_questions is not None else existing.open_questions,
            recent_agent_contributions=(
                extracted_contributions if extracted_contributions is not None
                else existing.recent_agent_contributions
            ),
            important_constraints=(
                extracted_constraints if extracted_constraints is not None
                else existing.important_constraints
            ),
            last_updated_at=utcnow(),
            updated_after_turn_id=synthesis_turn_id or existing.updated_after_turn_id,
        )

        success = await self.database_service.update_room_memory_by_room_id(
            room_id, room_memory
        )
        if success:
            logger.info(
                "RoomMemoryService.update_room_summary: "
                "Updated room summary for room %s", room_id,
            )
        else:
            logger.error(
                "RoomMemoryService.update_room_summary: "
                "Failed to persist room summary for room %s", room_id,
            )
        return success


# Singleton exports
chat_memory_service = ChatMemoryService()
room_memory_service = RoomMemoryService()
