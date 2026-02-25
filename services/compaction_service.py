"""
Compaction Service for lossless context compression.

This service implements pointer-based compaction (NOT summarization).
Full content is stored in MongoDB and replaced with references in context.
Original content is always retrievable on demand.

See CONTEXT_MEMORY_SYSTEM_DESIGN.md §6 for design details.
"""

from common.utils.logger import get_logger
from common.utils.time import utcnow
from config.settings import settings
from models.compaction import (
    CompactionConfig,
    CompactionResult,
    ContentReference,
    StorageType,
)
from models.memory import ConversationTurn, RoomMemory, TurnRepresentation
from services.content_storage_service import (
    ContentExpiredError,
    content_storage_service,
    hash_content,
)
from services.database_service import db_service

logger = get_logger(__name__)


def get_compaction_config() -> CompactionConfig:
    """
    Build CompactionConfig from settings.

    Returns:
        CompactionConfig with values from environment/settings
    """
    return CompactionConfig(
        enabled=settings.compaction_enabled,
        max_full_turns=settings.compaction_max_full_turns,
        max_total_tokens=settings.compaction_max_total_tokens,
        preserve_recent_turns=settings.compaction_preserve_recent,
        content_ttl_days=settings.compaction_content_ttl_days,
    )


class CompactionService:
    """
    Service for lossless compaction of conversation turns.

    Compaction replaces full content with pointers while preserving originals
    in storage. Zero information loss - agents can always fetch full content
    on demand via the fetch_turn_content tool.

    Key operations:
    - compact_room_memory: Compact older turns in a room
    - expand_turn_content: Retrieve full content for a compacted turn
    - should_compact: Check if a room needs compaction

    See CONTEXT_MEMORY_SYSTEM_DESIGN.md §6 for specification.
    """

    def __init__(self):
        self.content_storage = content_storage_service
        self.db_service = db_service

    async def should_compact(self, room_id: str) -> bool:
        """
        Check if room memory needs compaction.

        Compaction is triggered when:
        - Number of FULL turns exceeds max_full_turns, OR
        - Total tokens in FULL turns exceeds max_total_tokens

        See CONTEXT_MEMORY_SYSTEM_DESIGN.md §6.5 for specification.

        Args:
            room_id: The room ID to check

        Returns:
            True if compaction should be triggered
        """
        config = get_compaction_config()

        if not config.enabled:
            return False

        room_memory = await self.db_service.get_room_memory_by_room_id(room_id)
        if not room_memory:
            return False

        history = room_memory.get_conversation_history()
        if not history:
            return False

        # Count only full-representation turns
        full_turns = [
            t for t in history if t.representation == TurnRepresentation.FULL
        ]

        if not full_turns:
            return False

        # Check turn count threshold
        if len(full_turns) > config.max_full_turns:
            logger.debug(
                f"CompactionService: Room {room_id} has {len(full_turns)} full turns "
                f"(threshold: {config.max_full_turns})"
            )
            return True

        # Check token threshold
        token_estimate = sum(t.estimated_tokens_full for t in full_turns)
        if token_estimate > config.max_total_tokens:
            logger.debug(
                f"CompactionService: Room {room_id} has {token_estimate} tokens "
                f"(threshold: {config.max_total_tokens})"
            )
            return True

        return False

    async def compact_room_memory(self, room_id: str) -> CompactionResult:
        """
        Compact older conversation turns by replacing full content with pointers.

        IMPORTANT: This is LOSSLESS. Original content is preserved in storage
        and can be retrieved on demand.

        Process:
        1. Load room memory
        2. Identify turns to compact (older than preserve_recent_turns)
        3. For each turn: upsert full content (idempotent) -> replace with pointer
        4. Update room memory with compact representations

        Design constraints (§6.3):
        1. Idempotent: If the server crashes between store_full_content and
           save_room_memory, re-running compaction must not create duplicate documents.
           Achieved by using upsert on a unique (room_id, turn_id) index.
        2. Trigger location matters: This function is safe to call within the
           per-room processing lock (on-demand after synthesis).

        Args:
            room_id: The room ID to compact

        Returns:
            CompactionResult with statistics
        """
        config = get_compaction_config()

        if not config.enabled:
            return CompactionResult(
                room_id=room_id,
                compacted_count=0,
                tokens_saved=0,
                errors=["Compaction is disabled"],
            )

        room_memory = await self.db_service.get_room_memory_by_room_id(room_id)
        if not room_memory:
            return CompactionResult(
                room_id=room_id,
                compacted_count=0,
                tokens_saved=0,
                errors=[f"Room memory not found for room {room_id}"],
            )

        history = room_memory.get_conversation_history()
        if not history:
            return CompactionResult(
                room_id=room_id,
                compacted_count=0,
                tokens_saved=0,
            )

        preserve_count = config.preserve_recent_turns

        # Guard: preserve_count=0 means compact everything.
        # Python's seq[:-0] == seq[:0] == [] — NOT seq[:], so we must handle this.
        if preserve_count == 0:
            turns_to_compact = [
                t
                for t in history
                if t.representation == TurnRepresentation.FULL
            ]
        else:
            turns_to_compact = [
                t
                for t in history[:-preserve_count]
                if t.representation == TurnRepresentation.FULL
            ]

        if not turns_to_compact:
            logger.debug(
                f"CompactionService: No turns to compact for room {room_id}"
            )
            return CompactionResult(
                room_id=room_id,
                compacted_count=0,
                tokens_saved=0,
            )

        tokens_saved = 0
        compacted_count = 0
        errors: list[str] = []

        for turn in turns_to_compact:
            try:
                did_compact = await self._compact_single_turn(turn, room_id)
                if did_compact:
                    tokens_saved += (
                        turn.estimated_tokens_full - turn.estimated_tokens_compact
                    )
                    compacted_count += 1
            except Exception as e:
                error_msg = f"Failed to compact turn {turn.turn_id}: {e}"
                logger.error(f"CompactionService: {error_msg}")
                errors.append(error_msg)

        # Update room memory with compacted turns
        if compacted_count > 0:
            # Update the conversation history in room_memory
            # The turns were modified in place, so we just need to save
            room_memory.total_compactions += 1
            room_memory.last_activity_at = utcnow()

            # Write back to the same field that get_conversation_history() sourced from
            if room_memory.conversation_history:
                room_memory.conversation_history = history
            elif room_memory.memory_content and room_memory.memory_content.conversation_history:
                room_memory.memory_content.conversation_history = history
            else:
                room_memory.conversation_history = history

            await self.db_service.update_room_memory_by_room_id(
                room_id, room_memory
            )

            logger.info(
                f"CompactionService: Compacted {compacted_count} turns for room {room_id}, "
                f"saved ~{tokens_saved} tokens"
            )

        return CompactionResult(
            room_id=room_id,
            compacted_count=compacted_count,
            tokens_saved=tokens_saved,
            errors=errors,
        )

    async def _compact_single_turn(
        self, turn: ConversationTurn, room_id: str
    ) -> bool:
        """
        Compact a single turn by storing content and creating reference.

        Modifies the turn in place.

        Args:
            turn: The turn to compact (modified in place)
            room_id: The room ID

        Returns:
            True if the turn was compacted, False if skipped
        """
        if turn.representation == TurnRepresentation.COMPACT:
            return False

        if not turn.content:
            logger.warning(
                f"CompactionService: Turn {turn.turn_id} has no content to compact"
            )
            return False

        # 1. Upsert full content to MongoDB (IDEMPOTENT via unique index)
        content_doc_id = await self.content_storage.upsert_full_content(
            room_id=room_id,
            turn_id=turn.turn_id,
            content=turn.content,
            content_type=turn.content_type.value,
            turn_notes=turn.turn_notes,
        )

        # 2. Create reference pointer with content_hash for cache validation (§6.3)
        turn.content_ref = ContentReference(
            storage_type=StorageType.MONGODB,
            collection="conversation_content",
            document_id=content_doc_id,
            content_hash=hash_content(turn.content),
            created_at=utcnow(),
        )

        # 3. Optionally populate brief_summary for very old turns (>50 in history)
        # This is deferred - can be added later with a background job

        # 4. Switch to compact representation
        turn.content = None  # Remove full content from context
        turn.representation = TurnRepresentation.COMPACT

        return True

    async def expand_turn_content(self, turn: ConversationTurn) -> str:
        """
        Expand a compacted turn back to full content.

        Called ONLY when an agent explicitly requests the full content of a specific
        compacted turn (e.g., via a fetch_turn_content tool call). NOT called
        proactively during context assembly.

        See CONTEXT_MEMORY_SYSTEM_DESIGN.md §6.4 for specification.

        Args:
            turn: The turn to expand

        Returns:
            The full content string

        Raises:
            ContentExpiredError: If the stored document is missing
            ValueError: If the turn is compact but has no content_ref
        """
        if turn.representation == TurnRepresentation.FULL:
            return turn.content or ""

        if not turn.content_ref:
            raise ValueError(
                f"Compact turn {turn.turn_id} missing content reference"
            )

        return await self.content_storage.expand_content_reference(
            turn.content_ref, turn.turn_id
        )

    async def fetch_turn_content(
        self, turn_id: str, room_id: str
    ) -> str:
        """
        Tool callable by agents to retrieve full content of a compacted turn.

        Returns the full content string, or a descriptive error if unavailable.

        See CONTEXT_MEMORY_SYSTEM_DESIGN.md §6.4 for specification.

        Args:
            turn_id: The turn ID to fetch
            room_id: The room ID

        Returns:
            The full content string, or an error message
        """
        room_memory = await self.db_service.get_room_memory_by_room_id(room_id)
        if not room_memory:
            return f"[Error: Room {room_id} not found]"

        history = room_memory.get_conversation_history()
        turn = next(
            (t for t in history if t.turn_id == turn_id), None
        )

        if turn is None:
            return f"[Error: Turn {turn_id} not found in room history]"

        try:
            return await self.expand_turn_content(turn)
        except ContentExpiredError:
            return f"[Error: Content for turn {turn_id} is no longer available (expired)]"
        except ValueError as e:
            return f"[Error: {e}]"

    async def expand_turns_for_context(
        self, turns: list[ConversationTurn]
    ) -> list[ConversationTurn]:
        """
        Prepare turns for inclusion in a context window.

        Strategy (recency-only — matches Manus reference design):
        - FULL turns: included as-is.
        - COMPACT turns: included as pointer strings.
          They are NOT expanded proactively. If an agent needs the full content
          of a compacted turn, it must request it explicitly via fetch_turn_content.

        Why not expand based on query relevance?
        - Compact turns have content=None; relevance can only be assessed from metadata
        - Proactive expansion of all turns defeats the purpose of compaction
        - The Manus reference design uses recency only and lets agents request content
          explicitly — this is simpler and correct

        See CONTEXT_MEMORY_SYSTEM_DESIGN.md §6.4 for specification.

        Args:
            turns: List of turns to prepare

        Returns:
            The turns list unchanged (assembly layer calls turn.to_context_string())
        """
        return turns  # Assembly layer calls turn.to_context_string() for rendering

    async def get_compaction_stats(self, room_id: str) -> dict:
        """
        Get compaction statistics for a room.

        Args:
            room_id: The room ID

        Returns:
            Dict with compaction statistics
        """
        room_memory = await self.db_service.get_room_memory_by_room_id(room_id)
        if not room_memory:
            return {"error": f"Room {room_id} not found"}

        history = room_memory.get_conversation_history()

        full_turns = [
            t for t in history if t.representation == TurnRepresentation.FULL
        ]
        compact_turns = [
            t for t in history if t.representation == TurnRepresentation.COMPACT
        ]

        full_tokens = sum(t.estimated_tokens_full for t in full_turns)
        compact_tokens_saved = sum(
            t.estimated_tokens_full - t.estimated_tokens_compact
            for t in compact_turns
        )

        content_stats = await self.content_storage.get_content_stats_for_room(
            room_id
        )

        return {
            "room_id": room_id,
            "total_turns": len(history),
            "full_turns": len(full_turns),
            "compact_turns": len(compact_turns),
            "full_tokens": full_tokens,
            "tokens_saved_by_compaction": compact_tokens_saved,
            "total_compactions": room_memory.total_compactions,
            "content_storage": content_stats,
        }


# Singleton export
compaction_service = CompactionService()
