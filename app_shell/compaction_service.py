"""
Compaction Service for lossless context compression.

This service implements pointer-based compaction (NOT summarization).
Full content is stored in MongoDB and replaced with references in context.
Original content is always retrievable on demand.

See CONTEXT_MEMORY_SYSTEM_DESIGN.md §6 for design details.
"""

import os

from app_shell.database_service import db_service
from common.utils.context_utils import estimate_tokens
from common.utils.logger import get_logger
from common.utils.time import utcnow
from models.compaction import (
    CompactionResult,
    ContentReference,
    StorageType,
)
from models.memory import ConversationTurn, RoomMemory, TurnRepresentation
from platform_module.content_storage import (
    ContentExpiredError,
    hash_content,
)

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


def _is_unsupported_storage_response(content: str) -> bool:
    return (
        isinstance(content, str)
        and content.startswith("[Error:")
        and "unsupported storage" in content.lower()
    )


class _UnboundContentStorage:
    def _raise_unbound(self):
        raise RuntimeError(
            "CompactionService.bind_content_storage() not called - startup incomplete"
        )

    async def upsert_full_content(self, *args, **kwargs):
        self._raise_unbound()

    async def expand_content_reference(self, *args, **kwargs):
        self._raise_unbound()


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
        self.content_storage = _UnboundContentStorage()
        self.db_service = db_service
        self._facade = None
        self._bound = False

    def bind_content_storage(self, content_storage) -> None:
        self.content_storage = content_storage

    def bind_facade(self, facade) -> None:
        self._facade = facade
        self._bound = True

    def _require_facade(self):
        if not self._bound or self._facade is None:
            raise RuntimeError(
                "CompactionService.bind_facade() not called - startup incomplete"
            )
        return self._facade

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
        facade = self._require_facade()
        return await facade.should_compact(room_id)

    async def compact_if_needed(self, room_id: str) -> CompactionResult | None:
        """Check and compact in a single pass, avoiding redundant DB loads.

        Returns the CompactionResult if compaction ran, or None if it was
        not needed (or disabled).
        """
        facade = self._require_facade()
        return _legacy_compaction_result_or_none(
            await facade.compact_if_needed(room_id)
        )

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
        facade = self._require_facade()
        return _legacy_compaction_result(
            await facade.compact_room_memory(
                room_id,
                room_memory_doc=room_memory.model_dump(mode="json")
                if room_memory is not None
                else None,
            )
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
            from app_shell.memory_search_service import memory_search_service

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
        if turn.representation == TurnRepresentation.FULL:
            return turn.content or ""
        if turn.content_ref is None:
            raise ValueError(
                f"Compact turn {turn.turn_id} has missing content reference"
            )
        if turn.content_ref.storage_type != StorageType.MONGODB:
            return await self.content_storage.expand_content_reference(
                turn.content_ref,
                turn.turn_id,
            )
        facade = self._require_facade()
        return await facade.expand_turn_content_from_turn(
            turn.model_dump(mode="json")
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
        facade = self._require_facade()
        try:
            content = await facade.fetch_turn_content(turn_id, room_id)
        except NotImplementedError:
            return await self._fetch_turn_content_via_legacy_storage(turn_id, room_id)
        if not _is_unsupported_storage_response(content):
            return content
        return await self._fetch_turn_content_via_legacy_storage(turn_id, room_id)

    async def _fetch_turn_content_via_legacy_storage(
        self, turn_id: str, room_id: str
    ) -> str:
        room_memory = await self.db_service.get_room_memory_by_room_id(room_id)
        if not room_memory:
            return f"[Error: Room {room_id} not found]"

        history = room_memory.get_conversation_history()
        turn = next(
            (item for item in history if item.turn_id == turn_id),
            None,
        )
        if turn is None:
            return f"[Error: Turn {turn_id} not found in room history]"

        try:
            return await self.expand_turn_content(turn)
        except ContentExpiredError:
            return f"[Error: Content for turn {turn_id} is no longer available (expired)]"
        except NotImplementedError as exc:
            return f"[Error: Content for turn {turn_id} uses unsupported storage: {exc}]"
        except ValueError as exc:
            return f"[Error: {exc}]"

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
        self._require_facade()
        return turns  # Assembly layer calls turn.to_context_string() for rendering

    async def get_compaction_stats(self, room_id: str) -> dict:
        """
        Get compaction statistics for a room.

        Args:
            room_id: The room ID

        Returns:
            Dict with compaction statistics
        """
        facade = self._require_facade()
        return await facade.get_compaction_stats(room_id)


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
