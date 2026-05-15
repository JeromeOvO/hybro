"""
Compaction Service for lossless context compression.

This service implements pointer-based compaction (NOT summarization).
Full content is stored in MongoDB and replaced with references in context.
Original content is always retrievable on demand.

See CONTEXT_MEMORY_SYSTEM_DESIGN.md §6 for design details.
"""

import asyncio
import os

from common.utils.context_utils import estimate_tokens
from common.utils.logger import get_logger
from common.utils.time import utcnow
from models.compaction import (
    CompactionResult,
    ContentReference,
    StorageType,
)
from models.context_config import compaction_config
from models.memory import ConversationTurn, RoomMemory, TurnRepresentation
from services.content_storage_service import (
    ContentExpiredError,
    content_storage_service,
    hash_content,
)
from services.database_service import db_service

logger = get_logger(__name__)

# Concurrency limit for parallel compaction I/O (content storage + Pinecone).
# Tune based on downstream service capacity. Default 5 balances throughput vs.
# rate-limit risk.  Must be >= 1; invalid values fall back to default.
_DEFAULT_COMPACTION_CONCURRENCY = 5
try:
    COMPACTION_CONCURRENCY = max(1, int(os.getenv("COMPACTION_CONCURRENCY", str(_DEFAULT_COMPACTION_CONCURRENCY))))
except (ValueError, TypeError):
    COMPACTION_CONCURRENCY = _DEFAULT_COMPACTION_CONCURRENCY


def _safe_tokens_full(turn: ConversationTurn) -> int:
    """Return estimated_tokens_full, falling back to estimate_tokens() for legacy turns."""
    if turn.estimated_tokens_full > 0:
        return turn.estimated_tokens_full
    if turn.content:
        return estimate_tokens(turn.content)
    return 0


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
        self._facade = None
        self._bound = False

    def bind_facade(self, facade) -> None:
        self._facade = facade
        self._bound = True

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
        if self._bound and self._facade is not None:
            return await self._facade.should_compact(room_id)

        config = compaction_config

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
        token_estimate = sum(_safe_tokens_full(t) for t in full_turns)
        if token_estimate > config.max_total_tokens:
            logger.debug(
                f"CompactionService: Room {room_id} has {token_estimate} tokens "
                f"(threshold: {config.max_total_tokens})"
            )
            return True

        return False

    async def compact_if_needed(self, room_id: str) -> CompactionResult | None:
        """Check and compact in a single pass, avoiding redundant DB loads.

        Returns the CompactionResult if compaction ran, or None if it was
        not needed (or disabled).
        """
        if self._bound and self._facade is not None:
            return _legacy_compaction_result_or_none(
                await self._facade.compact_if_needed(room_id)
            )

        config = compaction_config
        if not config.enabled:
            return None

        room_memory = await self.db_service.get_room_memory_by_room_id(room_id)
        if not room_memory:
            return None

        history = room_memory.get_conversation_history()
        if not history:
            return None

        full_turns = [
            t for t in history if t.representation == TurnRepresentation.FULL
        ]
        if not full_turns:
            return None

        needs = len(full_turns) > config.max_full_turns
        if not needs:
            token_estimate = sum(_safe_tokens_full(t) for t in full_turns)
            needs = token_estimate > config.max_total_tokens

        if not needs:
            return None

        return await self.compact_room_memory(room_id, room_memory=room_memory)

    async def compact_room_memory(
        self,
        room_id: str,
        room_memory: RoomMemory | None = None,
    ) -> CompactionResult:
        """
        Compact older conversation turns by replacing full content with pointers.

        IMPORTANT: This is LOSSLESS. Original content is preserved in storage
        and can be retrieved on demand.

        Process:
        1. Load room memory (or reuse pre-loaded instance)
        2. Identify turns to compact (older than preserve_recent_turns)
        3. Concurrently prepare each turn (bounded by semaphore):
           upsert full content (idempotent) + index for search -> collect pointer data
        4. Atomic bulk_write to mark turns compact in MongoDB (no full-doc rewrite)

        Design constraints (§6.3):
        1. Idempotent: If the server crashes between store_full_content and
           the bulk_write, re-running compaction must not create duplicate documents.
           Achieved by using upsert on a unique (room_id, turn_id) index.
        2. Trigger location matters: This function is safe to call within the
           per-room processing lock (on-demand after synthesis).
        """
        if self._bound and self._facade is not None:
            return _legacy_compaction_result(
                await self._facade.compact_room_memory(
                    room_id,
                    room_memory_doc=room_memory.model_dump(mode="json")
                    if room_memory is not None
                    else None,
                )
            )

        config = compaction_config

        if not config.enabled:
            return CompactionResult(
                room_id=room_id,
                compacted_count=0,
                tokens_saved=0,
                errors=["Compaction is disabled"],
            )

        if not room_memory:
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
        compacted_entries: list[dict] = []
        errors: list[str] = []

        sem = asyncio.Semaphore(COMPACTION_CONCURRENCY)

        async def _compact_one(turn: ConversationTurn) -> tuple[dict | None, int, str | None]:
            """Prepare one turn for compaction under a semaphore."""
            async with sem:
                try:
                    ref_data = await self._prepare_compaction(turn, room_id)
                    if ref_data:
                        saved = max(
                            0, turn.estimated_tokens_full - turn.estimated_tokens_compact
                        )
                        return ref_data, saved, None
                    return None, 0, None
                except Exception as e:
                    msg = f"Failed to compact turn {turn.turn_id}: {e}"
                    logger.error("CompactionService: %s", msg)
                    return None, 0, msg

        results = await asyncio.gather(
            *(_compact_one(turn) for turn in turns_to_compact)
        )

        for ref_data, saved, error_msg in results:
            if ref_data:
                compacted_entries.append(ref_data)
                tokens_saved += saved
            if error_msg:
                errors.append(error_msg)

        if compacted_entries:
            save_success = await self.db_service.compact_turns_bulk(
                room_id, compacted_entries
            )

            if save_success:
                logger.info(
                    f"CompactionService: Compacted {len(compacted_entries)} turns for room {room_id}, "
                    f"saved ~{tokens_saved} tokens"
                )
            else:
                error_msg = (
                    f"CompactionService: Prepared {len(compacted_entries)} turns in-memory "
                    f"but atomic write failed for room {room_id} — will retry next cycle"
                )
                logger.warning(error_msg)
                errors.append(error_msg)
                compacted_entries = []
                tokens_saved = 0

        return CompactionResult(
            room_id=room_id,
            compacted_count=len(compacted_entries),
            tokens_saved=tokens_saved,
            errors=errors,
        )

    async def _prepare_compaction(
        self, turn: ConversationTurn, room_id: str
    ) -> dict | None:
        """
        Prepare a single turn for compaction: store content + index in Pinecone.

        Returns a dict with {turn_id, content_ref, estimated_tokens_compact}
        ready for compact_turns_bulk, or None if the turn should be skipped.
        Does NOT mutate the turn object.
        """
        if turn.representation == TurnRepresentation.COMPACT:
            return None

        if not turn.content:
            logger.warning(
                f"CompactionService: Turn {turn.turn_id} has no content to compact"
            )
            return None

        # 1. Upsert full content to MongoDB (IDEMPOTENT via unique index)
        content_doc_id = await self.content_storage.upsert_full_content(
            room_id=room_id,
            turn_id=turn.turn_id,
            content=turn.content,
            content_type=turn.content_type.value,
            turn_notes=turn.turn_notes,
        )

        # 2. Index the turn in Pinecone for vector search (Phase 4, §8).
        # Indexing failure is non-blocking: compaction proceeds regardless so that
        # a Pinecone outage doesn't stall all compaction.
        #
        # TODO(reconciliation): Implement a background reconciliation worker that:
        #   1. Queries conversation_content for documents without a corresponding
        #      Pinecone vector (track via `indexed_at` field or separate collection).
        #   2. Re-indexes missing turns with exponential backoff.
        #   3. Runs on a cron schedule (e.g., every 5 minutes).
        # Until implemented, un-indexed turns will be missing from vector search
        # but still retrievable via keyword search and direct expansion.
        try:
            from services.memory_search_service import memory_search_service

            indexed = await memory_search_service.index_turn_for_search(turn, room_id)
            if not indexed:
                logger.warning(
                    "CompactionService: Pinecone indexing failed for turn %s "
                    "in room %s — compaction will proceed; index retry needed",
                    turn.turn_id,
                    room_id,
                )
        except Exception as e:
            logger.warning(
                "CompactionService: Pinecone indexing error for turn %s "
                "in room %s: %s — compaction will proceed; index retry needed",
                turn.turn_id,
                room_id,
                e,
            )

        # 3. Build reference pointer
        content_ref = ContentReference(
            storage_type=StorageType.MONGODB,
            collection="conversation_content",
            document_id=content_doc_id,
            content_hash=hash_content(turn.content),
            created_at=utcnow(),
        )

        return {
            "turn_id": turn.turn_id,
            "content_ref": content_ref.model_dump(mode="json"),
            "estimated_tokens_compact": turn.estimated_tokens_compact,
        }

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
        if self._bound and self._facade is not None:
            return await self._facade.expand_turn_content_from_turn(
                turn.model_dump(mode="json")
            )

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
        if self._bound and self._facade is not None:
            return await self._facade.fetch_turn_content(turn_id, room_id)

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
        except NotImplementedError as e:
            return f"[Error: Content for turn {turn_id} uses unsupported storage: {e}]"
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
        if self._bound and self._facade is not None:
            return await self._facade.get_compaction_stats(room_id)

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

        full_tokens = sum(_safe_tokens_full(t) for t in full_turns)
        compact_tokens_saved = sum(
            max(0, t.estimated_tokens_full - t.estimated_tokens_compact)
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


def _legacy_compaction_result(result) -> CompactionResult:
    metadata = result.metadata or {}
    return CompactionResult(
        room_id=result.room_id,
        compacted_count=result.compacted_count,
        tokens_saved=result.tokens_saved,
        errors=list(metadata.get("errors") or []),
        compacted_at=metadata.get("compacted_at") or utcnow(),
    )


def _legacy_compaction_result_or_none(result) -> CompactionResult | None:
    if result is None:
        return None
    if (result.metadata or {}).get("skipped"):
        return None
    return _legacy_compaction_result(result)
