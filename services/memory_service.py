import asyncio
from uuid import uuid4

from common.utils.context_utils import (
    add_turn_to_history,
    clean_mention_format,
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
        attachments = request.attachments

        room_memory = await self.database_service.get_room_memory_by_room_id(room_id)

        if not room_memory:
            # Initialize new room memory with structured format
            memory_content = MemoryContent()

            if new_message:
                # Clean @mentions before storing
                clean_message = clean_mention_format(new_message, room_agent_set)
                from services.room_services import build_turn_content

                turn_content = build_turn_content(clean_message, attachments)
                memory_content = add_turn_to_history(
                    memory_content=memory_content,
                    role="user",
                    content=turn_content,
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
            if new_message:
                clean_message = clean_mention_format(new_message, room_agent_set)

                from common.utils.context_utils import (
                    MAX_HISTORY_TURNS,
                    MAX_SUMMARY_CHARS,
                    estimate_tokens,
                    extract_turn_notes,
                )
                from models.memory import ConversationTurn, TurnRole
                from services.room_services import build_turn_content

                turn_content = build_turn_content(clean_message, attachments)
                turn = ConversationTurn(
                    role=TurnRole.USER,
                    content=turn_content,
                    user_id=user_id,
                    estimated_tokens_full=estimate_tokens(turn_content),
                    turn_notes=extract_turn_notes(turn_content),
                )

                modified, matched = await self.database_service.push_and_trim_conversation_turn(
                    room_id,
                    turn.model_dump(mode="json"),
                    max_turns=MAX_HISTORY_TURNS,
                    summary_stub=f"[User] {clean_message[:200]}...",
                    max_summary_chars=MAX_SUMMARY_CHARS,
                )
                if not modified:
                    logger.error("RoomMemoryService: Failed to push user turn to room %s", room_id)
                    return RoomCenterMemoryResponse(
                        room_id=room_id,
                        success=False,
                        error="Room memory not found" if not matched else "Failed to update room memory",
                        status_code=404 if not matched else 500,
                    )

        # Track user interaction (§4.3 UserMemory)
        await self._track_user_interaction(user_id)

        return RoomCenterMemoryResponse(
            room_id=room_id,
            memory_id=room_memory.memory_id,
            memory=room_memory,
            success=True,
            error=None,
            status_code=200,
        )

    async def _track_user_interaction(self, user_id: str | None) -> None:
        """Fire-and-forget: increment user interaction counter in UserMemory (§4.3)."""
        if not user_id:
            return
        try:
            await self.database_service.increment_user_interactions(user_id)
        except Exception as e:
            logger.debug("UserMemory tracking skipped: %s", e)

    async def _track_agent_call(
        self,
        agent_id: str,
        success: bool,
        response_time_ms: float = 0.0,
    ) -> None:
        """Fire-and-forget: record agent call outcome in AgentMemory (§4.4)."""
        try:
            await self.database_service.record_agent_call(
                agent_id=agent_id,
                success=success,
                response_time_ms=response_time_ms,
            )
        except Exception as e:
            logger.debug("AgentMemory tracking skipped: %s", e)

    async def _enrich_turn_notes_background(
        self,
        room_id: str,
        turn_id: str,
        heuristic_notes: dict | None,
        content: str,
    ) -> None:
        """Background task: call LLM to extract richer turn_notes, then
        atomically update just that turn in MongoDB via positional $ operator.
        Failures are logged and swallowed — heuristic notes from Save #1 remain."""
        try:
            from common.utils.context_utils import extract_turn_notes_llm

            enriched_notes = await extract_turn_notes_llm(content)
            if enriched_notes and enriched_notes != heuristic_notes:
                await self.database_service.update_turn_notes(
                    room_id, turn_id, enriched_notes,
                )
        except Exception as e:
            logger.debug(
                "RoomMemoryService: background turn_notes enrichment failed "
                "for room %s turn %s: %s", room_id, turn_id, e,
            )

    async def add_agent_response_to_memory(
        self,
        room_id: str,
        agent_id: str,
        agent_name: str,
        response_text: str,
        was_successful: bool = True,
    ) -> RoomCenterMemoryResponse:
        """
        Add an agent's response to the room conversation history.
        Called after an agent completes its response.

        Uses atomic $push instead of loading the full document.
        """
        from common.utils.context_utils import (
            MAX_HISTORY_TURNS,
            MAX_SUMMARY_CHARS,
            estimate_tokens,
            extract_turn_notes,
        )
        from models.memory import ConversationTurn, TurnRole

        tokens_full = estimate_tokens(response_text)
        notes = extract_turn_notes(response_text)

        turn = ConversationTurn(
            role=TurnRole.AGENT,
            content=response_text,
            agent_id=agent_id,
            agent_name=agent_name,
            estimated_tokens_full=tokens_full,
            turn_notes=notes,
            was_successful=was_successful,
        )

        modified, matched = await self.database_service.push_and_trim_conversation_turn(
            room_id,
            turn.model_dump(mode="json"),
            max_turns=MAX_HISTORY_TURNS,
            summary_stub=f"[{agent_name}] {response_text[:200]}...",
            max_summary_chars=MAX_SUMMARY_CHARS,
        )

        if not modified:
            logger.error(
                "RoomMemoryService: Failed to push agent response to room %s",
                room_id,
            )
            return RoomCenterMemoryResponse(
                room_id=room_id,
                success=False,
                error="Room memory not found" if not matched else "Failed to update room memory",
                status_code=404 if not matched else 500,
            )

        # Post-save: enrich turn_notes via LLM for long turns (§6.2).
        # Fire-and-forget: the LLM call + targeted DB write run in a background
        # task so the caller isn't blocked by the extra 1-2s round-trip.
        try:
            from common.utils.context_utils import LLM_TURN_NOTES_THRESHOLD

            if response_text and tokens_full > LLM_TURN_NOTES_THRESHOLD:
                asyncio.create_task(
                    self._enrich_turn_notes_background(
                        room_id, turn.turn_id, notes, response_text,
                    )
                )
        except Exception as e:
            logger.debug(
                "RoomMemoryService: LLM turn_notes enrichment skipped: %s", e
            )

        # Track agent call outcome (§4.4 AgentMemory)
        await self._track_agent_call(
            agent_id=agent_id,
            success=was_successful,
        )

        return RoomCenterMemoryResponse(
            room_id=room_id,
            success=True,
            error=None,
            status_code=200,
        )

    async def add_synthesis_to_history(
        self,
        room_id: str,
        synthesis_text: str,
        trajectory: "SupervisorTrajectory | None" = None,
    ) -> str | None:
        """
        Add supervisor synthesis text to room conversation history (§11.3).

        Creates a SUPERVISOR-role turn with the synthesis content and atomically
        pushes it to MongoDB using $push (no full-document read-modify-write).

        Returns the new turn_id on success (needed by update_room_summary
        to populate RoomSummary.updated_after_turn_id per §4.2).

        When trajectory is provided, agent contributions are extracted into the
        turn for richer turn_notes (forward-compatibility with Phase 4B search).
        """
        from common.utils.context_utils import (
            MAX_HISTORY_TURNS,
            MAX_SUMMARY_CHARS,
            estimate_tokens,
            extract_turn_notes,
        )
        from models.memory import ConversationTurn, TurnRole, TurnType

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

        tokens_full = estimate_tokens(enriched_content)
        notes = extract_turn_notes(enriched_content)

        turn = ConversationTurn(
            role=TurnRole.SUPERVISOR,
            content=enriched_content,
            turn_type=TurnType.MESSAGE,
            estimated_tokens_full=tokens_full,
            turn_notes=notes,
        )

        summary_stub = (
            f"[Supervisor synthesis ({turn.turn_id[:8]})] "
            f"{enriched_content[:200]}..."
        )
        modified, matched = await self.database_service.push_and_trim_conversation_turn(
            room_id,
            turn.model_dump(mode="json"),
            max_turns=MAX_HISTORY_TURNS,
            summary_stub=summary_stub,
            max_summary_chars=MAX_SUMMARY_CHARS,
        )
        if not modified:
            if not matched:
                logger.error(
                    "RoomMemoryService.add_synthesis_to_history: "
                    "Room memory not found for room %s", room_id,
                )
            else:
                logger.error(
                    "RoomMemoryService.add_synthesis_to_history: "
                    "Failed to persist synthesis turn for room %s", room_id,
                )
            return None

        # Post-save: enrich turn_notes via LLM for long synthesis turns (§6.2).
        # Synthesis turns are high-value, context-dense text — worth the LLM call.
        try:
            from common.utils.context_utils import LLM_TURN_NOTES_THRESHOLD

            if enriched_content and tokens_full > LLM_TURN_NOTES_THRESHOLD:
                asyncio.create_task(
                    self._enrich_turn_notes_background(
                        room_id, turn.turn_id, notes, enriched_content,
                    )
                )
        except Exception as e:
            logger.debug(
                "RoomMemoryService.add_synthesis_to_history: "
                "background enrichment schedule failed: %s", e,
            )

        return turn.turn_id

    async def update_room_summary(
        self,
        room_id: str,
        synthesis_text: str,
        synthesis_turn_id: str | None = None,
    ) -> bool:
        """
        Update RoomMemory.room_summary using LLM extraction from synthesis text (§9, §11.3).

        Uses a lightweight projection to load only room_summary + room_facts,
        then writes back with an atomic $set (no full-document rewrite).
        This is safe to run concurrently with add_synthesis_to_history and
        compact_room_memory — they touch disjoint fields.

        Returns:
            True if successfully updated, False if extraction or persistence failed
        """
        extraction_prompt = (
            "Extract structured room summary fields from the following synthesis. "
            "Return ONLY valid JSON with these keys:\n"
            '- "current_goal": string or null — what the user/room is trying to accomplish\n'
            '- "key_decisions": list of strings — decisions that should persist\n'
            '- "open_questions": list of strings — unresolved questions or blockers\n'
            '- "recent_agent_contributions": list of strings — last 3-5 agent result summaries\n'
            '- "important_constraints": list of strings — hard constraints stated\n'
            '- "room_facts": list of strings — durable facts worth remembering across sessions '
            '(e.g. user preferences, project names, deadlines, technical constraints). '
            "Only include facts NOT already obvious from the goal or decisions. "
            "Return an empty list if there are no new facts.\n\n"
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

        from models.memory import RoomFact, RoomSummary

        # Lightweight projection — only loads room_summary and room_facts
        doc = await self.database_service.get_room_summary_projection(room_id)
        if not doc:
            logger.warning(
                "RoomMemoryService.update_room_summary: "
                "Room memory not found for room %s", room_id,
            )
            return False

        existing = RoomSummary(**(doc.get("room_summary") or {}))
        extracted_goal = extracted.get("current_goal")
        extracted_decisions = extracted.get("key_decisions")
        extracted_questions = extracted.get("open_questions")
        extracted_contributions = extracted.get("recent_agent_contributions")
        extracted_constraints = extracted.get("important_constraints")

        new_summary = RoomSummary(
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

        # Deduplicate new facts against existing ones
        new_facts: list[dict] = []
        extracted_facts_raw = extracted.get("room_facts", [])
        if isinstance(extracted_facts_raw, list) and extracted_facts_raw:
            existing_fact_contents = {
                (f.get("content") or "").lower().strip()
                for f in (doc.get("room_facts") or [])
            }
            for fact_text in extracted_facts_raw:
                if (
                    isinstance(fact_text, str)
                    and fact_text.strip()
                    and fact_text.lower().strip() not in existing_fact_contents
                ):
                    new_facts.append(
                        RoomFact(
                            content=fact_text.strip(),
                            source_turn_id=synthesis_turn_id,
                        ).model_dump(mode="json")
                    )
                    existing_fact_contents.add(fact_text.lower().strip())

        MAX_ROOM_FACTS = 50
        success = await self.database_service.update_room_summary_atomic(
            room_id,
            new_summary.model_dump(mode="json"),
            new_facts=new_facts if new_facts else None,
            max_facts=MAX_ROOM_FACTS,
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
