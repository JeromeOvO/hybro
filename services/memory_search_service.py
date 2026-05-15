"""
Memory Search Service for hybrid retrieval across conversation history.

Implements:
- Vector similarity search via Pinecone (semantic)
- BM25 keyword search via MongoDB text index on turn_notes (§8.3)
- Weighted merge of results
- Temporal decay (recency boost)
- MMR (Maximal Marginal Relevance) for diversity
- Turn indexing at compaction time (write path)

See CONTEXT_MEMORY_SYSTEM_DESIGN.md §8 for design specification.
"""

import asyncio
import math
import time
from datetime import datetime

from pinecone.exceptions import NotFoundException as PineconeNotFoundException

from common.utils.logger import get_logger
from common.utils.time import utcnow
from database.mongodb import mongodb
from database.pinecone_db import pinecone_db
from models.context_config import memory_search_config
from models.memory import ConversationTurn
from models.search import (
    MemorySearchResponse,
    MemorySearchResult,
    MemorySourceType,
)
from services.openai_service import openai_service

logger = get_logger(__name__)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors using pure Python.

    Returns a value in [-1, 1]. For normalized embeddings this is equivalent
    to the dot product, but we compute the full formula for safety.
    """
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class MemorySearchService:
    """Hybrid search across memory layers. Config loaded from env.

    Search pipeline:
    1. Vector search (Pinecone) — semantic similarity
    2. Keyword search (MongoDB $text on turn_notes) — BM25 matching
    3. Weighted merge of results
    4. Temporal decay — recency boost
    5. MMR — diversity re-ranking

    Write path:
    - index_turn_for_search() — embed and upsert a turn to Pinecone

    See CONTEXT_MEMORY_SYSTEM_DESIGN.md §8.1 for specification.
    """

    def __init__(self):
        self.openai_service = openai_service
        self.pinecone = pinecone_db
        self._index_available: bool | None = None
        self._facade = None
        self._bound = False

    def bind_facade(self, facade) -> None:
        self._facade = facade
        self._bound = True

    @property
    def config(self):
        return memory_search_config

    @property
    def _pinecone_index(self):
        """Lazily-connected Pinecone index for memory search."""
        return self.pinecone.get_index(self.config.index_name)

    def _is_index_available(self) -> bool:
        """Check (and cache) whether the Pinecone index exists.

        On the first call, probes the index with a lightweight
        describe_index_stats request. The result is cached for the
        lifetime of the process — the index either exists or it
        doesn't (creating it requires a restart to pick up anyway).
        """
        if self._index_available is not None:
            return self._index_available
        try:
            self._pinecone_index.describe_index_stats()
            self._index_available = True
            logger.info(
                "MemorySearch: Pinecone index '%s' is available",
                self.config.index_name,
            )
        except PineconeNotFoundException:
            self._index_available = False
            logger.warning(
                "MemorySearch: Pinecone index '%s' not found — "
                "vector search/indexing will be skipped until restart",
                self.config.index_name,
            )
        except Exception as e:
            logger.warning(
                "MemorySearch: failed to probe Pinecone index '%s': %s — "
                "will retry on next request",
                self.config.index_name,
                e,
            )
            return False
        return self._index_available

    @property
    def _content_collection(self):
        """MongoDB conversation_content collection."""
        return mongodb.conversation_content_collection

    # =========================================================================
    # Public API: Search
    # =========================================================================

    async def search(
        self,
        query: str,
        room_id: str,
        user_id: str | None = None,
    ) -> MemorySearchResponse:
        """Hybrid search combining vector similarity and keyword matching.

        All weights and parameters loaded from environment config.

        Args:
            query: The search query text
            room_id: Room to search within
            user_id: Optional user ID for future personalization

        Returns:
            MemorySearchResponse with ranked results
        """
        if self._bound and self._facade is not None:
            return _legacy_search_response(
                await self._facade.legacy_search(query, room_id, user_id=user_id)
            )

        start_time = time.monotonic()

        if not self.config.enabled:
            return MemorySearchResponse(
                query=query,
                room_id=room_id,
                results=[],
                vector_search_used=False,
                keyword_search_used=False,
                temporal_decay_applied=False,
                mmr_applied=False,
            )

        # Run vector and keyword searches in parallel
        vector_task = self._vector_search(query, room_id)
        keyword_task = self._keyword_search(query, room_id)
        raw_results = await asyncio.gather(
            vector_task, keyword_task, return_exceptions=True
        )

        vector_results: list[MemorySearchResult] = []
        keyword_results: list[MemorySearchResult] = []
        vector_used = True
        keyword_used = True

        if isinstance(raw_results[0], Exception):
            logger.warning(
                f"MemorySearch: vector search failed for room {room_id}: "
                f"{raw_results[0]}"
            )
            vector_used = False
        else:
            vector_results = raw_results[0]
            if not self._is_index_available():
                vector_used = False

        if isinstance(raw_results[1], Exception):
            logger.warning(
                f"MemorySearch: keyword search failed for room {room_id}: "
                f"{raw_results[1]}"
            )
            keyword_used = False
        else:
            keyword_results = raw_results[1]

        # Merge with weights
        merged = self._merge_results(
            vector_results,
            keyword_results,
            vector_weight=self.config.vector_weight,
            keyword_weight=self.config.keyword_weight,
        )

        # Temporal decay
        decay_applied = False
        if self.config.temporal_decay_enabled and merged:
            merged = self._apply_temporal_decay(
                merged,
                half_life_days=self.config.half_life_days,
            )
            decay_applied = True

        # MMR for diversity
        mmr_applied = False
        if merged:
            merged = self._apply_mmr(
                merged,
                lambda_param=self.config.mmr_lambda,
            )
            mmr_applied = True

        final = merged[: self.config.max_results]

        # Hydrate results that have empty content (vector-only matches)
        # by fetching one_liner from conversation_content collection
        empty_content_ids = [r.turn_id for r in final if not r.content and r.turn_id]
        if empty_content_ids:
            await self._hydrate_results_from_storage(final, room_id)

        elapsed_ms = (time.monotonic() - start_time) * 1000

        return MemorySearchResponse(
            query=query,
            room_id=room_id,
            results=final,
            total_matches=len(merged),
            search_time_ms=round(elapsed_ms, 2),
            searched_at=utcnow(),
            vector_search_used=vector_used,
            keyword_search_used=keyword_used,
            temporal_decay_applied=decay_applied,
            mmr_applied=mmr_applied,
        )

    # =========================================================================
    # Public API: Indexing (write path)
    # =========================================================================

    async def index_turn_for_search(
        self,
        turn: ConversationTurn,
        room_id: str,
    ) -> bool:
        """Embed a turn's content and upsert to the Pinecone memory index.

        Called at compaction time so that compacted turns become searchable
        via vector similarity. Recent FULL turns are already in the context
        window, so they don't need indexing.

        Args:
            turn: The conversation turn to index (should still have content)
            room_id: Room the turn belongs to

        Returns:
            True if indexed successfully, False otherwise
        """
        if self._bound and self._facade is not None:
            turn_doc = turn.model_dump(mode="json") if hasattr(turn, "model_dump") else turn
            return await self._facade.index_turn_for_search(room_id, turn_doc)

        content = turn.content
        if not content:
            logger.debug(
                f"MemorySearch: skipping index for turn {turn.turn_id} "
                f"— no content"
            )
            return False

        if not self._is_index_available():
            return False

        try:
            embedding = await self.openai_service.get_embedding(content)

            metadata = {
                "room_id": room_id,
                "turn_id": turn.turn_id,
                "role": turn.role.value if turn.role else "unknown",
                "agent_name": turn.agent_name or "",
                "timestamp": turn.timestamp.isoformat()
                if turn.timestamp
                else "",
            }

            await asyncio.to_thread(
                self._pinecone_index.upsert,
                vectors=[
                    {
                        "id": turn.turn_id,
                        "values": embedding,
                        "metadata": metadata,
                    }
                ],
            )

            logger.debug(
                f"MemorySearch: indexed turn {turn.turn_id} for room {room_id}"
            )
            return True

        except PineconeNotFoundException:
            logger.debug(
                "MemorySearch: Pinecone index '%s' not found — skipping indexing",
                self.config.index_name,
            )
            return False
        except Exception as e:
            logger.warning(
                f"MemorySearch: failed to index turn {turn.turn_id}: {e}"
            )
            return False

    async def delete_room_index(self, room_id: str) -> bool:
        """Delete all indexed vectors for a room.

        Args:
            room_id: Room to delete vectors for

        Returns:
            True if deletion succeeded, False otherwise
        """
        if self._bound and self._facade is not None:
            return await self._facade.delete_room_index(room_id)

        if not self._is_index_available():
            return False

        try:
            await asyncio.to_thread(
                self._pinecone_index.delete,
                filter={"room_id": {"$eq": room_id}},
            )
            logger.info(
                f"MemorySearch: deleted index entries for room {room_id}"
            )
            return True
        except PineconeNotFoundException:
            logger.debug(
                "MemorySearch: Pinecone index '%s' not found — skipping deletion",
                self.config.index_name,
            )
            return False
        except Exception as e:
            logger.warning(
                f"MemorySearch: failed to delete index for room {room_id}: {e}"
            )
            return False

    # =========================================================================
    # Private: Result hydration
    # =========================================================================

    async def _hydrate_results_from_storage(
        self,
        results: list[MemorySearchResult],
        room_id: str,
    ) -> None:
        """Populate empty content/content_preview from conversation_content docs.

        Vector-only results arrive with content="" because Pinecone only stores
        metadata. This fetches the one_liner from turn_notes stored at compaction
        time so the result is useful to the supervisor.
        """
        needs_hydration = {r.turn_id for r in results if not r.content and r.turn_id}
        if not needs_hydration:
            return

        try:
            cursor = self._content_collection.find(
                {"room_id": room_id, "turn_id": {"$in": list(needs_hydration)}},
                {"turn_id": 1, "turn_notes": 1},
            )
            docs_by_turn: dict[str, dict] = {}
            async for doc in cursor:
                docs_by_turn[doc.get("turn_id", "")] = doc

            for r in results:
                if r.turn_id in docs_by_turn and not r.content:
                    notes = docs_by_turn[r.turn_id].get("turn_notes")
                    if isinstance(notes, dict):
                        one_liner = notes.get("one_liner", "")
                        if one_liner:
                            r.content = one_liner[:self.config.max_snippet_chars]
                            r.content_preview = one_liner[:self.config.max_snippet_chars]
        except Exception as e:
            logger.debug("MemorySearch: hydration failed, results may lack content: %s", e)

    # =========================================================================
    # Private: Vector search (Pinecone)
    # =========================================================================

    async def _vector_search(
        self, query: str, room_id: str
    ) -> list[MemorySearchResult]:
        """Semantic vector search via Pinecone.

        Embeds the query and searches the room-memory index filtered to
        the target room_id.
        """
        if not self._is_index_available():
            return []

        embedding = await self.openai_service.get_embedding(query)

        try:
            results = await asyncio.to_thread(
                self._pinecone_index.query,
                vector=embedding,
                top_k=50,
                include_metadata=True,
                filter={"room_id": {"$eq": room_id}},
            )
        except PineconeNotFoundException:
            logger.debug(
                "MemorySearch: Pinecone index '%s' not found — skipping vector search",
                self.config.index_name,
            )
            return []

        matches = getattr(results, "matches", []) if results else []
        search_results = []

        for match in matches:
            metadata = match.get("metadata", {}) if isinstance(match, dict) else getattr(match, "metadata", {})
            score = match.get("score", 0.0) if isinstance(match, dict) else getattr(match, "score", 0.0)
            match_id = match.get("id", "") if isinstance(match, dict) else getattr(match, "id", "")

            timestamp = None
            ts_str = metadata.get("timestamp", "")
            if ts_str:
                try:
                    timestamp = datetime.fromisoformat(ts_str)
                except (ValueError, TypeError):
                    pass

            search_results.append(
                MemorySearchResult(
                    turn_id=metadata.get("turn_id", match_id),
                    room_id=room_id,
                    source_type=MemorySourceType.TURN,
                    content="",  # Will be populated from MongoDB if needed
                    vector_score=score,
                    timestamp=timestamp,
                    role=metadata.get("role"),
                    agent_name=metadata.get("agent_name") or None,
                    is_compact=True,
                    can_expand=True,
                )
            )

        return search_results

    # =========================================================================
    # Private: Keyword search (MongoDB text index on turn_notes)
    # =========================================================================

    async def _keyword_search(
        self, query: str, room_id: str
    ) -> list[MemorySearchResult]:
        """BM25 keyword search over conversation_content collection.

        For FULL turns: searches full `content` text.
        For COMPACT turns: searches turn_notes.keywords + turn_notes.entities +
                           turn_notes.one_liner — no content expansion needed.

        Uses the MongoDB text index on (content, turn_notes.keywords,
        turn_notes.entities, turn_notes.one_liner) created in
        create_context_memory_indexes().

        See CONTEXT_MEMORY_SYSTEM_DESIGN.md §8.3 for specification.
        """
        cursor = (
            self._content_collection.find(
                {"room_id": room_id, "$text": {"$search": query}},
                {
                    "score": {"$meta": "textScore"},
                    "turn_id": 1,
                    "turn_notes": 1,
                    "content_type": 1,
                    "stored_at": 1,
                },
            )
            .sort([("score", {"$meta": "textScore"})])
            .limit(50)
        )

        docs = await cursor.to_list(length=50)
        results = []

        for doc in docs:
            text_score = doc.get("score", 0.0)
            turn_notes = doc.get("turn_notes", {})
            one_liner = ""
            if isinstance(turn_notes, dict):
                one_liner = turn_notes.get("one_liner", "")

            preview = one_liner[:self.config.max_snippet_chars] if one_liner else ""

            timestamp = doc.get("stored_at")

            results.append(
                MemorySearchResult(
                    turn_id=doc.get("turn_id", ""),
                    room_id=room_id,
                    source_type=MemorySourceType.TURN,
                    content=one_liner or f"[{doc.get('content_type', 'text')}]",
                    content_preview=preview or None,
                    keyword_score=text_score,
                    timestamp=timestamp,
                    is_compact=True,
                    can_expand=True,
                )
            )

        return results

    # =========================================================================
    # Private: Result merging
    # =========================================================================

    @staticmethod
    def _merge_results(
        vector_results: list[MemorySearchResult],
        keyword_results: list[MemorySearchResult],
        vector_weight: float,
        keyword_weight: float,
    ) -> list[MemorySearchResult]:
        """Merge vector and keyword results with weighted scoring.

        Results are merged by turn_id. If a turn appears in both sets,
        its scores are combined with the configured weights.
        """
        by_turn: dict[str, MemorySearchResult] = {}

        # Normalize scores within each result set to [0, 1]
        v_max = max((r.vector_score for r in vector_results), default=1.0) or 1.0
        k_max = max((r.keyword_score for r in keyword_results), default=1.0) or 1.0

        for r in vector_results:
            key = r.turn_id or ""
            if not key:
                continue
            normalized = r.vector_score / v_max
            entry = by_turn.get(key)
            if entry is None:
                by_turn[key] = r.model_copy()
                by_turn[key].vector_score = normalized
            else:
                entry.vector_score = normalized

        for r in keyword_results:
            key = r.turn_id or ""
            if not key:
                continue
            normalized = r.keyword_score / k_max
            entry = by_turn.get(key)
            if entry is None:
                by_turn[key] = r.model_copy()
                by_turn[key].keyword_score = normalized
            else:
                entry.keyword_score = normalized
                # Prefer keyword result's content/preview if richer
                if r.content and not entry.content:
                    entry.content = r.content
                if r.content_preview and not entry.content_preview:
                    entry.content_preview = r.content_preview
                if r.timestamp and not entry.timestamp:
                    entry.timestamp = r.timestamp

        for result in by_turn.values():
            result.combined_score = (
                vector_weight * result.vector_score
                + keyword_weight * result.keyword_score
            )

        return sorted(
            by_turn.values(),
            key=lambda r: r.combined_score,
            reverse=True,
        )

    # =========================================================================
    # Private: Temporal decay
    # =========================================================================

    @staticmethod
    def _apply_temporal_decay(
        results: list[MemorySearchResult],
        half_life_days: int,
    ) -> list[MemorySearchResult]:
        """Apply exponential temporal decay to combined scores.

        Multiplies each result's combined_score by 2^(-age/half_life),
        giving recent results a natural recency boost.
        """
        now = utcnow()
        if half_life_days <= 0:
            return results

        for r in results:
            if r.timestamp:
                ts = r.timestamp
                # Ensure both are tz-aware or both naive for safe subtraction
                if ts.tzinfo is None:
                    age_days = (now.replace(tzinfo=None) - ts).total_seconds() / 86400
                else:
                    age_days = (now - ts).total_seconds() / 86400
                decay = math.pow(2, -age_days / half_life_days)
            else:
                decay = 0.5  # Unknown timestamp gets modest penalty
            r.temporal_decay_factor = decay
            r.combined_score *= decay

        return sorted(results, key=lambda r: r.combined_score, reverse=True)

    # =========================================================================
    # Private: MMR (Maximal Marginal Relevance)
    # =========================================================================

    @staticmethod
    def _apply_mmr(
        results: list[MemorySearchResult],
        lambda_param: float,
    ) -> list[MemorySearchResult]:
        """Re-rank results using Maximal Marginal Relevance for diversity.

        MMR selects results that balance relevance (combined_score) with
        novelty (dissimilarity to already-selected results).

        Since we don't store embeddings on MemorySearchResult, we use the
        score profile [vector_score, keyword_score, temporal_decay_factor]
        as a lightweight proxy for diversity estimation.

        lambda_param: 1.0 = pure relevance, 0.0 = pure diversity.
        """
        if len(results) <= 1:
            return results

        # Build proxy vectors for diversity computation
        profiles: dict[int, list[float]] = {}
        for i, r in enumerate(results):
            profiles[i] = [r.vector_score, r.keyword_score, r.temporal_decay_factor]

        selected_indices: list[int] = []
        remaining = set(range(len(results)))

        # First pick: highest combined_score
        first = max(remaining, key=lambda i: results[i].combined_score)
        selected_indices.append(first)
        remaining.discard(first)

        while remaining:
            best_idx = -1
            best_mmr = float("-inf")

            for i in remaining:
                relevance = results[i].combined_score

                max_sim = max(
                    _cosine_similarity(profiles[i], profiles[s])
                    for s in selected_indices
                )

                mmr = lambda_param * relevance - (1 - lambda_param) * max_sim
                if mmr > best_mmr:
                    best_mmr = mmr
                    best_idx = i

            if best_idx < 0:
                break

            selected_indices.append(best_idx)
            remaining.discard(best_idx)

        return [results[i] for i in selected_indices]


# Singleton export
memory_search_service = MemorySearchService()


def _legacy_search_response(payload: dict) -> MemorySearchResponse:
    results = []
    for item in payload.get("results") or []:
        metadata = getattr(item, "metadata", {}) or {}
        source_type = metadata.get("source_type") or MemorySourceType.TURN
        if isinstance(source_type, str):
            source_type = MemorySourceType(source_type)
        results.append(
            MemorySearchResult(
                turn_id=metadata.get("turn_id") or getattr(item, "source_message_id", None),
                fact_id=metadata.get("fact_id"),
                room_id=getattr(item, "room_id", payload.get("room_id")),
                source_type=source_type,
                content=getattr(item, "content", ""),
                content_preview=metadata.get("content_preview"),
                vector_score=metadata.get("vector_score", 0.0),
                keyword_score=metadata.get("keyword_score", 0.0),
                combined_score=getattr(item, "score", 0.0),
                temporal_decay_factor=metadata.get("temporal_decay_factor", 1.0),
                timestamp=metadata.get("timestamp"),
                role=metadata.get("role"),
                agent_name=metadata.get("agent_name"),
                is_compact=metadata.get("is_compact", False),
                can_expand=metadata.get("can_expand", False),
            )
        )
    return MemorySearchResponse(
        query=payload.get("query", ""),
        room_id=payload.get("room_id", ""),
        results=results,
        total_matches=payload.get("total_matches", len(results)),
        search_time_ms=payload.get("search_time_ms", 0.0),
        searched_at=payload.get("searched_at") or utcnow(),
        vector_search_used=payload.get("vector_search_used", True),
        keyword_search_used=payload.get("keyword_search_used", True),
        temporal_decay_applied=payload.get("temporal_decay_applied", True),
        mmr_applied=payload.get("mmr_applied", True),
    )
