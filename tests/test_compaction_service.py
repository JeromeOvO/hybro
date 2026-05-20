"""
Unit tests for Compaction Service and Content Storage Service.

Tests cover:
- Content storage (upsert, retrieval, idempotency)
- Compaction process (turn compaction, token savings)
- Expansion (on-demand content retrieval)
- Round-trip (compact -> expand -> verify)
- Error handling (missing content, expired content)

See CONTEXT_MEMORY_SYSTEM_DESIGN.md §6 for design specification.
"""

import asyncio

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from common.dto import CompactionResult as DtoCompactionResult
from common.utils.time import utcnow
from models.compaction import (
    ContentReference,
    StorageType,
    StoredContent,
)
from models.memory import (
    ConversationTurn,
    ContentType,
    MemoryContent,
    RoomMemory,
    TurnRepresentation,
    TurnRole,
)
from services.compaction_service import (
    CompactionService,
)
from services.content_storage_service import (
    ContentExpiredError,
    ContentStorageService,
    hash_content,
)


# =============================================================================
# Test Fixtures
# =============================================================================


class BoundContentStorageFacade:
    def __init__(self, service: ContentStorageService):
        self.service = service

    async def content_upsert_full_content(
        self,
        *,
        room_id: str,
        turn_id: str,
        content: str,
        content_type: str,
        turn_notes: dict | None = None,
    ) -> str:
        from config.settings import settings

        content_hash = hash_content(content)
        now = utcnow()
        expires_at = None
        if settings.compaction_content_ttl_days > 0:
            from datetime import timedelta

            expires_at = now + timedelta(
                days=settings.compaction_content_ttl_days
            )

        insert_doc = {
            "room_id": room_id,
            "turn_id": turn_id,
            "content": content,
            "content_type": content_type,
            "content_hash": content_hash,
            "stored_at": now,
            "expires_at": expires_at,
        }
        if turn_notes:
            insert_doc["turn_notes"] = turn_notes

        result = await self.service.collection.update_one(
            {"room_id": room_id, "turn_id": turn_id},
            {"$setOnInsert": insert_doc},
            upsert=True,
        )
        if result.upserted_id:
            return str(result.upserted_id)

        existing = await self.service.collection.find_one(
            {"room_id": room_id, "turn_id": turn_id}, {"_id": 1}
        )
        if existing:
            return str(existing["_id"])

        raise RuntimeError(f"Failed to upsert content for turn {turn_id}")

    async def content_get_content_by_document_id(self, document_id: str) -> str | None:
        from bson import ObjectId

        try:
            doc = await self.service.collection.find_one({"_id": ObjectId(document_id)})
            if doc:
                return doc.get("content")
            return None
        except Exception:
            return None

    async def content_get_content_by_turn_id(self, room_id: str, turn_id: str) -> str | None:
        doc = await self.service.collection.find_one({"room_id": room_id, "turn_id": turn_id})
        return doc.get("content") if doc else None

    async def content_expand_mongodb_reference(self, content_ref: dict, turn_id: str) -> str:
        document_id = content_ref.get("document_id")
        if not document_id:
            raise ValueError(f"ContentReference for turn {turn_id} has no document_id")
        content = await self.content_get_content_by_document_id(document_id)
        if content is None:
            raise ContentExpiredError(turn_id, document_id)
        return content

    async def content_delete_content_by_turn_id(self, room_id: str, turn_id: str) -> bool:
        result = await self.service.collection.delete_one({"room_id": room_id, "turn_id": turn_id})
        return result.deleted_count > 0

    async def content_delete_content_by_room_id(self, room_id: str) -> int:
        result = await self.service.collection.delete_many({"room_id": room_id})
        return result.deleted_count

    async def content_get_content_stats_for_room(self, room_id: str) -> dict:
        pipeline = [
            {"$match": {"room_id": room_id}},
            {
                "$group": {
                    "_id": "$content_type",
                    "count": {"$sum": 1},
                    "total_size": {"$sum": {"$strLenBytes": "$content"}},
                }
            },
        ]
        cursor = self.service.collection.aggregate(pipeline)
        rows = await cursor.to_list(length=None)
        stats = {
            "room_id": room_id,
            "by_type": {},
            "total_documents": 0,
            "total_size_bytes": 0,
        }
        for row in rows:
            stats["by_type"][row["_id"]] = {
                "count": row["count"],
                "size_bytes": row["total_size"],
            }
            stats["total_documents"] += row["count"]
            stats["total_size_bytes"] += row["total_size"]
        return stats


def bind_content_storage_facade(service: ContentStorageService) -> ContentStorageService:
    service.bind_facade(BoundContentStorageFacade(service))
    return service


class BoundCompactionFacade:
    def __init__(self, service: CompactionService):
        self.service = service

    async def should_compact(self, room_id: str) -> bool:
        from models.context_config import compaction_config
        from services import compaction_service as compaction_module

        config = compaction_config
        if not config.enabled:
            return False
        room_memory = await self.service.db_service.get_room_memory_by_room_id(room_id)
        if not room_memory:
            return False
        history = room_memory.get_conversation_history()
        full_turns = [
            turn
            for turn in history
            if turn.representation == TurnRepresentation.FULL
        ]
        if not full_turns:
            return False
        if len(full_turns) > config.max_full_turns:
            return True
        token_estimate = sum(compaction_module._safe_tokens_full(turn) for turn in full_turns)
        return token_estimate > config.max_total_tokens

    async def compact_room_memory(
        self,
        room_id: str,
        room_memory_doc: dict | RoomMemory | None = None,
    ):
        from models.context_config import compaction_config
        from services import compaction_service as compaction_module

        config = compaction_config
        if not config.enabled:
            return DtoCompactionResult(
                room_id=room_id,
                compacted_count=0,
                tokens_saved=0,
                metadata={"errors": ["Compaction is disabled"]},
            )

        room_memory = room_memory_doc
        if isinstance(room_memory, dict):
            room_memory = RoomMemory(**room_memory)
        if not room_memory:
            room_memory = await self.service.db_service.get_room_memory_by_room_id(room_id)
        if not room_memory:
            return DtoCompactionResult(
                room_id=room_id,
                compacted_count=0,
                tokens_saved=0,
                metadata={"errors": [f"Room memory not found for room {room_id}"]},
            )

        history = room_memory.get_conversation_history()
        if not history:
            return DtoCompactionResult(room_id=room_id, compacted_count=0, tokens_saved=0)

        preserve_count = config.preserve_recent_turns
        if preserve_count == 0:
            turns_to_compact = [
                turn
                for turn in history
                if turn.representation == TurnRepresentation.FULL
            ]
        else:
            turns_to_compact = [
                turn
                for turn in history[:-preserve_count]
                if turn.representation == TurnRepresentation.FULL
            ]

        if not turns_to_compact:
            return DtoCompactionResult(room_id=room_id, compacted_count=0, tokens_saved=0)

        sem = asyncio.Semaphore(compaction_module.COMPACTION_CONCURRENCY)

        async def compact_one(turn: ConversationTurn):
            async with sem:
                try:
                    ref_data = await self.service._prepare_compaction(turn, room_id)
                    if ref_data:
                        saved = max(
                            0,
                            turn.estimated_tokens_full
                            - turn.estimated_tokens_compact,
                        )
                        return ref_data, saved, None
                    return None, 0, None
                except Exception as exc:
                    return None, 0, f"Failed to compact turn {turn.turn_id}: {exc}"

        prepared = await asyncio.gather(*(compact_one(turn) for turn in turns_to_compact))
        compacted_entries = [entry for entry, _saved, _error in prepared if entry]
        tokens_saved = sum(saved for entry, saved, _error in prepared if entry)
        errors = [error for _entry, _saved, error in prepared if error]

        if compacted_entries:
            save_success = await self.service.db_service.compact_turns_bulk(
                room_id,
                compacted_entries,
            )
            if not save_success:
                errors.append(
                    f"Prepared {len(compacted_entries)} turns in-memory "
                    f"but atomic write failed for room {room_id}"
                )
                compacted_entries = []
                tokens_saved = 0

        return DtoCompactionResult(
            room_id=room_id,
            compacted_count=len(compacted_entries),
            tokens_saved=tokens_saved,
            metadata={"errors": errors, "compacted_at": utcnow()},
        )

    async def expand_turn_content_from_turn(self, turn_doc: dict) -> str:
        return await self.expand_turn_content(ConversationTurn(**turn_doc))

    async def expand_turn_content(self, turn_doc: dict | ConversationTurn) -> str:
        turn = turn_doc if isinstance(turn_doc, ConversationTurn) else ConversationTurn(**turn_doc)
        if turn.representation == TurnRepresentation.FULL:
            return turn.content or ""
        if not turn.content_ref:
            raise ValueError(f"Compact turn {turn.turn_id} missing content reference")
        return await self.service.content_storage.expand_content_reference(
            turn.content_ref,
            turn.turn_id,
        )

    async def fetch_turn_content(self, turn_id: str, room_id: str) -> str:
        room_memory = await self.service.db_service.get_room_memory_by_room_id(room_id)
        if not room_memory:
            return f"[Error: Room {room_id} not found]"
        turn = next(
            (
                item
                for item in room_memory.get_conversation_history()
                if item.turn_id == turn_id
            ),
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


def bind_compaction_facade(service: CompactionService) -> CompactionService:
    service.bind_facade(BoundCompactionFacade(service))
    return service


@pytest.fixture
def mock_settings():
    """Mock settings for compaction configuration."""
    with patch("models.context_config.settings") as mock:
        mock.compaction_enabled = True
        mock.compaction_max_full_turns = 20
        mock.compaction_max_total_tokens = 80000
        mock.compaction_preserve_recent = 10
        mock.compaction_content_ttl_days = 0
        mock.memory_search_enabled = True
        mock.memory_search_vector_weight = 0.7
        mock.memory_search_keyword_weight = 0.3
        mock.memory_search_temporal_decay_enabled = True
        mock.memory_search_half_life_days = 30
        mock.memory_search_mmr_lambda = 0.7
        mock.memory_search_max_results = 10
        mock.memory_search_max_snippet_chars = 500
        mock.memory_search_index_name = "room-memory"
        yield mock


@pytest.fixture
def mock_content_settings():
    """Mock settings for content storage."""
    with patch("config.settings.settings") as mock:
        mock.compaction_content_ttl_days = 0
        yield mock


@pytest.fixture
def sample_turn() -> ConversationTurn:
    """Create a sample conversation turn for testing."""
    return ConversationTurn(
        turn_id=str(uuid4()),
        role=TurnRole.USER,
        content="This is a test message with some content that will be compacted.",
        content_type=ContentType.TEXT,
        representation=TurnRepresentation.FULL,
        estimated_tokens_full=50,
        estimated_tokens_compact=20,
        timestamp=datetime.now(),
    )


@pytest.fixture
def sample_agent_turn() -> ConversationTurn:
    """Create a sample agent turn for testing."""
    return ConversationTurn(
        turn_id=str(uuid4()),
        role=TurnRole.AGENT,
        agent_id="agent-123",
        agent_name="TestAgent",
        content="This is a detailed agent response with analysis and recommendations.",
        content_type=ContentType.AGENT_RESPONSE,
        representation=TurnRepresentation.FULL,
        estimated_tokens_full=100,
        estimated_tokens_compact=20,
        timestamp=datetime.now(),
    )


@pytest.fixture
def sample_room_memory(sample_turn, sample_agent_turn) -> RoomMemory:
    """Create a sample room memory with conversation history."""
    # Create 15 turns to test compaction (more than preserve_recent=10)
    turns = []
    for i in range(15):
        turn = ConversationTurn(
            turn_id=str(uuid4()),
            role=TurnRole.USER if i % 2 == 0 else TurnRole.AGENT,
            content=f"Message {i}: This is test content for turn number {i}.",
            content_type=ContentType.TEXT,
            representation=TurnRepresentation.FULL,
            estimated_tokens_full=40 + i * 5,
            estimated_tokens_compact=20,
            timestamp=datetime.now(),
        )
        if turn.role == TurnRole.AGENT:
            turn.agent_id = f"agent-{i}"
            turn.agent_name = f"Agent{i}"
        turns.append(turn)

    memory_content = MemoryContent(conversation_history=turns)

    return RoomMemory(
        room_id="test-room-123",
        memory_id=str(uuid4()),
        memory_content=memory_content,
        total_compactions=0,
    )


# =============================================================================
# Content Storage Service Tests
# =============================================================================


class TestHashContent:
    """Tests for hash_content utility function."""

    def test_hash_content_returns_hex_string(self):
        """Hash should return a hex-encoded string."""
        content = "test content"
        result = hash_content(content)
        assert isinstance(result, str)
        assert len(result) == 64  # SHA-256 produces 64 hex chars

    def test_hash_content_is_deterministic(self):
        """Same content should produce same hash."""
        content = "test content"
        hash1 = hash_content(content)
        hash2 = hash_content(content)
        assert hash1 == hash2

    def test_hash_content_different_for_different_content(self):
        """Different content should produce different hashes."""
        hash1 = hash_content("content 1")
        hash2 = hash_content("content 2")
        assert hash1 != hash2


# =============================================================================
# Compaction Service Tests
# =============================================================================


class TestCompactionConfig:
    """Tests for compaction_config singleton from context_config."""

    def test_reads_from_settings(self, mock_settings):
        """Should read compaction configuration from settings."""
        from models.context_config import compaction_config

        assert compaction_config.enabled is True
        assert compaction_config.max_full_turns == 20
        assert compaction_config.max_total_tokens == 80000
        assert compaction_config.preserve_recent_turns == 10


class TestCompactionService:
    """Tests for CompactionService."""

    @pytest.fixture
    def service(self):
        """Create a CompactionService instance."""
        return bind_compaction_facade(CompactionService())

    @pytest.fixture(autouse=True)
    def mock_memory_search(self):
        """Auto-mock memory_search_service.index_turn_for_search for all compaction tests."""
        with patch(
            "services.memory_search_service.memory_search_service"
        ) as mock:
            mock.index_turn_for_search = AsyncMock(return_value=True)
            yield mock

    @pytest.mark.asyncio
    async def test_should_compact_returns_false_when_disabled(
        self, service, mock_settings
    ):
        """Should return False when compaction is disabled."""
        mock_settings.compaction_enabled = False

        result = await service.should_compact("room-123")

        assert result is False

    @pytest.mark.asyncio
    async def test_should_compact_returns_false_when_no_room_memory(
        self, service, mock_settings
    ):
        """Should return False when room memory doesn't exist."""
        with patch.object(
            service.db_service, "get_room_memory_by_room_id", return_value=None
        ):
            result = await service.should_compact("room-123")

        assert result is False

    @pytest.mark.asyncio
    async def test_should_compact_returns_true_when_exceeds_turn_threshold(
        self, service, mock_settings, sample_room_memory
    ):
        """Should return True when full turns exceed threshold."""
        mock_settings.compaction_max_full_turns = 5  # Lower threshold

        with patch.object(
            service.db_service,
            "get_room_memory_by_room_id",
            return_value=sample_room_memory,
        ):
            result = await service.should_compact("test-room-123")

        assert result is True

    @pytest.mark.asyncio
    async def test_should_compact_returns_true_when_exceeds_token_threshold(
        self, service, mock_settings, sample_room_memory
    ):
        """Should return True when tokens exceed threshold."""
        mock_settings.compaction_max_full_turns = 100  # High turn threshold
        mock_settings.compaction_max_total_tokens = 100  # Low token threshold

        with patch.object(
            service.db_service,
            "get_room_memory_by_room_id",
            return_value=sample_room_memory,
        ):
            result = await service.should_compact("test-room-123")

        assert result is True

    @pytest.mark.asyncio
    async def test_compact_room_memory_returns_early_when_disabled(
        self, service, mock_settings
    ):
        """Should return early with error when compaction is disabled."""
        mock_settings.compaction_enabled = False

        result = await service.compact_room_memory("room-123")

        assert result.compacted_count == 0
        assert "disabled" in result.errors[0].lower()

    @pytest.mark.asyncio
    async def test_compact_room_memory_compacts_older_turns(
        self, service, mock_settings, sample_room_memory
    ):
        """Should compact turns older than preserve_recent threshold."""
        mock_settings.compaction_preserve_recent = 10

        # Mock the content storage
        mock_upsert = AsyncMock(return_value="doc-id-123")

        with patch.object(
            service.db_service,
            "get_room_memory_by_room_id",
            return_value=sample_room_memory,
        ):
            with patch.object(
                service.db_service,
                "compact_turns_bulk",
                return_value=True,
            ):
                with patch.object(
                    service.content_storage,
                    "upsert_full_content",
                    mock_upsert,
                ):
                    result = await service.compact_room_memory("test-room-123")

        # With 15 turns and preserve_recent=10, should compact 5 turns
        assert result.compacted_count == 5
        assert result.tokens_saved > 0
        assert len(result.errors) == 0

    @pytest.mark.asyncio
    async def test_compact_room_memory_populates_content_hash(
        self, service, mock_settings
    ):
        """Should populate content_hash in data sent to compact_turns_bulk (§6.3)."""
        from services.content_storage_service import hash_content

        turn = ConversationTurn(
            turn_id="turn-hash-test",
            role=TurnRole.USER,
            content="Test content for hash verification",
            content_type=ContentType.TEXT,
            representation=TurnRepresentation.FULL,
            estimated_tokens_full=50,
            estimated_tokens_compact=20,
            timestamp=datetime.now(),
        )
        memory_content = MemoryContent(conversation_history=[turn])
        room_memory = RoomMemory(
            room_id="test-room",
            memory_id=str(uuid4()),
            memory_content=memory_content,
        )

        mock_settings.compaction_preserve_recent = 0

        async def mock_upsert(*args, **kwargs):
            return "doc-id-123"

        mock_compact = AsyncMock(return_value=True)

        with patch.object(
            service.db_service,
            "get_room_memory_by_room_id",
            return_value=room_memory,
        ):
            with patch.object(
                service.db_service,
                "compact_turns_bulk",
                mock_compact,
            ):
                with patch.object(
                    service.content_storage,
                    "upsert_full_content",
                    mock_upsert,
                ):
                    await service.compact_room_memory("test-room")

        mock_compact.assert_awaited_once()
        compacted_turns = mock_compact.call_args[0][1]
        assert len(compacted_turns) == 1
        content_ref = compacted_turns[0]["content_ref"]
        assert content_ref["content_hash"] is not None
        expected_hash = hash_content("Test content for hash verification")
        assert content_ref["content_hash"] == expected_hash

    @pytest.mark.asyncio
    async def test_compact_room_memory_handles_preserve_zero(
        self, service, mock_settings, sample_room_memory
    ):
        """Should compact all turns when preserve_recent is 0."""
        mock_settings.compaction_preserve_recent = 0

        mock_upsert = AsyncMock(return_value="doc-id-123")

        with patch.object(
            service.db_service,
            "get_room_memory_by_room_id",
            return_value=sample_room_memory,
        ):
            with patch.object(
                service.db_service,
                "compact_turns_bulk",
                return_value=True,
            ):
                with patch.object(
                    service.content_storage,
                    "upsert_full_content",
                    mock_upsert,
                ):
                    result = await service.compact_room_memory("test-room-123")

        # Should compact all 15 turns
        assert result.compacted_count == 15

    @pytest.mark.asyncio
    async def test_expand_turn_content_returns_content_for_full_turn(
        self, service, sample_turn
    ):
        """Should return content directly for FULL representation turns."""
        content = await service.expand_turn_content(sample_turn)

        assert content == sample_turn.content

    @pytest.mark.asyncio
    async def test_expand_turn_content_retrieves_content_for_compact_turn(
        self, service
    ):
        """Should retrieve content from storage for COMPACT turns."""
        compact_turn = ConversationTurn(
            turn_id="turn-123",
            role=TurnRole.USER,
            content=None,
            representation=TurnRepresentation.COMPACT,
            content_ref=ContentReference(
                storage_type=StorageType.MONGODB,
                collection="conversation_content",
                document_id="doc-456",
                created_at=datetime.now(),
            ),
            timestamp=datetime.now(),
        )

        mock_expand = AsyncMock(return_value="Retrieved content from storage")

        with patch.object(
            service.content_storage,
            "expand_content_reference",
            mock_expand,
        ):
            content = await service.expand_turn_content(compact_turn)

        assert content == "Retrieved content from storage"
        mock_expand.assert_called_once()

    @pytest.mark.asyncio
    async def test_expand_turn_content_uses_content_storage_for_s3_turn(
        self, service
    ):
        compact_turn = ConversationTurn(
            turn_id="turn-s3",
            role=TurnRole.USER,
            content=None,
            representation=TurnRepresentation.COMPACT,
            content_ref=ContentReference(
                storage_type=StorageType.S3,
                s3_bucket="bucket",
                s3_key="key",
                created_at=datetime.now(),
            ),
            timestamp=datetime.now(),
        )
        service._facade.expand_turn_content_from_turn = AsyncMock(
            side_effect=NotImplementedError("s3")
        )
        mock_expand = AsyncMock(return_value="Retrieved S3 content")

        with patch.object(
            service.content_storage,
            "expand_content_reference",
            mock_expand,
        ):
            content = await service.expand_turn_content(compact_turn)

        assert content == "Retrieved S3 content"
        mock_expand.assert_awaited_once()
        service._facade.expand_turn_content_from_turn.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_expand_turn_content_raises_for_missing_ref(self, service):
        """Should raise ValueError for compact turn without content_ref."""
        compact_turn = ConversationTurn(
            turn_id="turn-123",
            role=TurnRole.USER,
            content=None,
            representation=TurnRepresentation.COMPACT,
            content_ref=None,
            timestamp=datetime.now(),
        )

        with pytest.raises(ValueError) as exc_info:
            await service.expand_turn_content(compact_turn)

        assert "missing content reference" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_fetch_turn_content_returns_content(
        self, service, sample_room_memory
    ):
        """Should return content for a valid turn."""
        turn_id = sample_room_memory.memory_content.conversation_history[0].turn_id

        with patch.object(
            service.db_service,
            "get_room_memory_by_room_id",
            return_value=sample_room_memory,
        ):
            content = await service.fetch_turn_content(turn_id, "test-room-123")

        assert "Message 0" in content

    @pytest.mark.asyncio
    async def test_fetch_turn_content_uses_facade_for_mongo_backed_fetch(
        self, service
    ):
        service._facade.fetch_turn_content = AsyncMock(return_value="Facade content")

        with patch.object(
            service.db_service,
            "get_room_memory_by_room_id",
            AsyncMock(side_effect=AssertionError("legacy DB should not be used")),
        ):
            content = await service.fetch_turn_content("turn-123", "test-room-123")

        assert content == "Facade content"
        service._facade.fetch_turn_content.assert_awaited_once_with(
            "turn-123",
            "test-room-123",
        )

    @pytest.mark.asyncio
    async def test_fetch_turn_content_returns_error_for_missing_room(self, service):
        """Should return error message for missing room."""
        with patch.object(
            service.db_service, "get_room_memory_by_room_id", return_value=None
        ):
            content = await service.fetch_turn_content("turn-123", "missing-room")

        assert "[Error:" in content
        assert "not found" in content.lower()

    @pytest.mark.asyncio
    async def test_fetch_turn_content_returns_error_for_missing_turn(
        self, service, sample_room_memory
    ):
        """Should return error message for missing turn."""
        with patch.object(
            service.db_service,
            "get_room_memory_by_room_id",
            return_value=sample_room_memory,
        ):
            content = await service.fetch_turn_content(
                "nonexistent-turn", "test-room-123"
            )

        assert "[Error:" in content
        assert "not found" in content.lower()

    @pytest.mark.asyncio
    async def test_fetch_turn_content_returns_error_for_not_implemented_storage(
        self, service, sample_room_memory
    ):
        """Should return graceful error (not raise) when storage type is not implemented."""
        from models.memory import TurnRepresentation

        turn = sample_room_memory.memory_content.conversation_history[0]
        turn.representation = TurnRepresentation.COMPACT
        turn.content = None
        turn.content_ref = ContentReference(
            storage_type=StorageType.URL,
            url="https://example.com/content.txt",
            created_at=datetime.now(),
        )

        with patch.object(
            service.db_service,
            "get_room_memory_by_room_id",
            return_value=sample_room_memory,
        ):
            with patch.object(
                service.content_storage,
                "expand_content_reference",
                AsyncMock(side_effect=NotImplementedError("url")),
            ):
                content = await service.fetch_turn_content(turn.turn_id, "test-room-123")

        assert "[Error:" in content
        assert "unsupported storage" in content.lower()

    @pytest.mark.asyncio
    async def test_fetch_turn_content_uses_content_storage_for_s3_turn(
        self, service, sample_room_memory
    ):
        turn = sample_room_memory.memory_content.conversation_history[0]
        turn.representation = TurnRepresentation.COMPACT
        turn.content = None
        turn.content_ref = ContentReference(
            storage_type=StorageType.S3,
            s3_bucket="bucket",
            s3_key="key",
            created_at=datetime.now(),
        )
        service._facade.fetch_turn_content = AsyncMock(
            return_value="[Error: Content for turn turn-s3 uses unsupported storage: s3]"
        )
        mock_expand = AsyncMock(return_value="S3 content")

        with patch.object(
            service.db_service,
            "get_room_memory_by_room_id",
            return_value=sample_room_memory,
        ):
            with patch.object(
                service.content_storage,
                "expand_content_reference",
                mock_expand,
            ):
                content = await service.fetch_turn_content(
                    turn.turn_id,
                    "test-room-123",
                )

        assert content == "S3 content"
        service._facade.fetch_turn_content.assert_awaited_once_with(
            turn.turn_id,
            "test-room-123",
        )
        mock_expand.assert_awaited_once_with(turn.content_ref, turn.turn_id)


# =============================================================================
# Round-Trip Tests
# =============================================================================


class TestCompactionRoundTrip:
    """Tests for complete compaction -> expansion round-trip."""

    @pytest.fixture
    def service(self):
        """Create a CompactionService instance."""
        return bind_compaction_facade(CompactionService())

    @pytest.fixture(autouse=True)
    def mock_memory_search(self):
        with patch("services.memory_search_service.memory_search_service") as mock:
            mock.index_turn_for_search = AsyncMock(return_value=True)
            yield mock

    @pytest.mark.asyncio
    async def test_compact_and_expand_preserves_content(
        self, service, mock_settings, sample_turn
    ):
        """Content should be identical after compact -> expand cycle."""
        original_content = sample_turn.content
        room_id = "test-room"

        # Create a room memory with just this turn
        memory_content = MemoryContent(conversation_history=[sample_turn])
        room_memory = RoomMemory(
            room_id=room_id,
            memory_id=str(uuid4()),
            memory_content=memory_content,
        )

        # Mock storage to actually store and retrieve content
        stored_content = {}

        async def mock_upsert(room_id, turn_id, content, content_type, turn_notes=None):
            doc_id = f"doc-{turn_id}"
            stored_content[doc_id] = content
            return doc_id

        async def mock_expand(content_ref, turn_id):
            return stored_content.get(content_ref.document_id)

        mock_settings.compaction_preserve_recent = 0  # Compact all

        with patch.object(
            service.db_service,
            "get_room_memory_by_room_id",
            return_value=room_memory,
        ):
            mock_compact = AsyncMock(return_value=True)
            with patch.object(
                service.db_service,
                "compact_turns_bulk",
                mock_compact,
            ):
                with patch.object(
                    service.content_storage,
                    "upsert_full_content",
                    mock_upsert,
                ):
                    # Compact the turn
                    result = await service.compact_room_memory(room_id)

        assert result.compacted_count == 1

        # Verify compact_turns_bulk was called with correct data
        mock_compact.assert_awaited_once()
        compacted_data = mock_compact.call_args[0][1]
        assert len(compacted_data) == 1
        assert compacted_data[0]["content_ref"] is not None

        # Simulate expand by retrieving from our storage mock
        from models.compaction import ContentReference
        content_ref = ContentReference(**compacted_data[0]["content_ref"])

        with patch.object(
            service.content_storage,
            "expand_content_reference",
            mock_expand,
        ):
            expanded_content = await service.expand_turn_content(
                ConversationTurn(
                    turn_id=sample_turn.turn_id,
                    role=sample_turn.role,
                    content=None,
                    representation=TurnRepresentation.COMPACT,
                    content_ref=content_ref,
                )
            )

        assert expanded_content == original_content

    @pytest.mark.asyncio
    async def test_idempotent_compaction(self, service, mock_settings, sample_turn):
        """Running compaction twice should not compact already-compact turns."""
        room_id = "test-room"
        memory_content = MemoryContent(conversation_history=[sample_turn])
        room_memory = RoomMemory(
            room_id=room_id,
            memory_id=str(uuid4()),
            memory_content=memory_content,
        )

        upsert_calls = []

        async def mock_upsert(room_id, turn_id, content, content_type, turn_notes=None):
            upsert_calls.append((room_id, turn_id))
            return f"doc-{turn_id}"

        mock_settings.compaction_preserve_recent = 0

        # First call: room has a FULL turn
        # Second call: room has the turn marked COMPACT (as MongoDB would after first call)
        compact_turn = ConversationTurn(
            turn_id=sample_turn.turn_id,
            role=sample_turn.role,
            content=None,
            representation=TurnRepresentation.COMPACT,
        )
        room_memory_after = RoomMemory(
            room_id=room_id,
            memory_id=str(uuid4()),
            memory_content=MemoryContent(conversation_history=[compact_turn]),
        )

        call_count = 0

        async def get_room_memory_side_effect(rid):
            nonlocal call_count
            call_count += 1
            return room_memory if call_count == 1 else room_memory_after

        with patch.object(
            service.db_service,
            "get_room_memory_by_room_id",
            side_effect=get_room_memory_side_effect,
        ):
            with patch.object(
                service.db_service,
                "compact_turns_bulk",
                return_value=True,
            ):
                with patch.object(
                    service.content_storage,
                    "upsert_full_content",
                    mock_upsert,
                ):
                    result1 = await service.compact_room_memory(room_id)
                    result2 = await service.compact_room_memory(room_id)

        assert result1.compacted_count == 1
        assert result2.compacted_count == 0
        assert len(upsert_calls) == 1


# =============================================================================
# Token Savings Tests
# =============================================================================


class TestTokenSavings:
    """Tests for token savings calculations."""

    @pytest.fixture
    def service(self):
        """Create a CompactionService instance."""
        return bind_compaction_facade(CompactionService())

    @pytest.fixture(autouse=True)
    def mock_memory_search(self):
        with patch("services.memory_search_service.memory_search_service") as mock:
            mock.index_turn_for_search = AsyncMock(return_value=True)
            yield mock

    @pytest.mark.asyncio
    async def test_token_savings_calculated_correctly(
        self, service, mock_settings
    ):
        """Token savings should equal sum of (full - compact) for each turn."""
        # Create turns with known token counts
        turns = []
        for i in range(5):
            turn = ConversationTurn(
                turn_id=str(uuid4()),
                role=TurnRole.USER,
                content=f"Content {i}",
                representation=TurnRepresentation.FULL,
                estimated_tokens_full=100,  # Each turn is 100 tokens full
                estimated_tokens_compact=20,  # Each turn is 20 tokens compact
                timestamp=datetime.now(),
            )
            turns.append(turn)

        memory_content = MemoryContent(conversation_history=turns)
        room_memory = RoomMemory(
            room_id="test-room",
            memory_id=str(uuid4()),
            memory_content=memory_content,
        )

        mock_settings.compaction_preserve_recent = 0  # Compact all

        async def mock_upsert(*args, **kwargs):
            return "doc-id"

        with patch.object(
            service.db_service,
            "get_room_memory_by_room_id",
            return_value=room_memory,
        ):
            with patch.object(
                service.db_service,
                "compact_turns_bulk",
                return_value=True,
            ):
                with patch.object(
                    service.content_storage,
                    "upsert_full_content",
                    mock_upsert,
                ):
                    result = await service.compact_room_memory("test-room")

        # 5 turns * (100 - 20) = 400 tokens saved
        assert result.tokens_saved == 400
        assert result.compacted_count == 5


# =============================================================================
# Error Handling Tests
# =============================================================================


class TestErrorHandling:
    """Tests for error handling in compaction service."""

    @pytest.fixture
    def service(self):
        """Create a CompactionService instance."""
        return bind_compaction_facade(CompactionService())

    @pytest.fixture(autouse=True)
    def mock_memory_search(self):
        with patch("services.memory_search_service.memory_search_service") as mock:
            mock.index_turn_for_search = AsyncMock(return_value=True)
            yield mock

    @pytest.mark.asyncio
    async def test_compaction_continues_on_single_turn_failure(
        self, service, mock_settings
    ):
        """Compaction should continue even if one turn fails."""
        turns = []
        for i in range(3):
            turn = ConversationTurn(
                turn_id=f"turn-{i}",
                role=TurnRole.USER,
                content=f"Content {i}",
                representation=TurnRepresentation.FULL,
                estimated_tokens_full=50,
                estimated_tokens_compact=20,
                timestamp=datetime.now(),
            )
            turns.append(turn)

        memory_content = MemoryContent(conversation_history=turns)
        room_memory = RoomMemory(
            room_id="test-room",
            memory_id=str(uuid4()),
            memory_content=memory_content,
        )

        mock_settings.compaction_preserve_recent = 0

        call_count = 0

        async def mock_upsert_with_failure(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:  # Fail on second turn
                raise Exception("Storage error")
            return f"doc-{call_count}"

        with patch.object(
            service.db_service,
            "get_room_memory_by_room_id",
            return_value=room_memory,
        ):
            with patch.object(
                service.db_service,
                "compact_turns_bulk",
                return_value=True,
            ):
                with patch.object(
                    service.content_storage,
                    "upsert_full_content",
                    mock_upsert_with_failure,
                ):
                    result = await service.compact_room_memory("test-room")

        # Should compact 2 turns (1st and 3rd), fail on 2nd
        assert result.compacted_count == 2
        assert len(result.errors) == 1
        assert "turn-1" in result.errors[0]

    @pytest.mark.asyncio
    async def test_compaction_proceeds_when_vector_indexing_fails(
        self, service, mock_settings, mock_memory_search
    ):
        """Compaction should proceed even if vector indexing fails (decoupled)."""
        turn = ConversationTurn(
            turn_id="turn-no-index",
            role=TurnRole.USER,
            content="Content that fails indexing",
            representation=TurnRepresentation.FULL,
            estimated_tokens_full=50,
            estimated_tokens_compact=20,
            timestamp=datetime.now(),
        )

        memory_content = MemoryContent(conversation_history=[turn])
        room_memory = RoomMemory(
            room_id="test-room",
            memory_id=str(uuid4()),
            memory_content=memory_content,
        )

        mock_settings.compaction_preserve_recent = 0
        mock_memory_search.index_turn_for_search = AsyncMock(return_value=False)

        with patch.object(
            service.db_service,
            "get_room_memory_by_room_id",
            return_value=room_memory,
        ):
            with patch.object(
                service.content_storage,
                "upsert_full_content",
                AsyncMock(return_value="doc-123"),
            ):
                with patch.object(
                    service.db_service,
                    "compact_turns_bulk",
                    return_value=True,
                ):
                    result = await service.compact_room_memory("test-room")

        assert result.compacted_count == 1
        assert turn.content == "Content that fails indexing"

    @pytest.mark.asyncio
    async def test_content_expired_error_has_correct_info(self):
        """ContentExpiredError should contain turn_id and document_id."""
        error = ContentExpiredError("turn-123", "doc-456")

        assert error.turn_id == "turn-123"
        assert error.document_id == "doc-456"
        assert "turn-123" in str(error)
        assert "doc-456" in str(error)


# =============================================================================
# Deserialization Round-Trip Tests (Bug Fix Verification)
# =============================================================================


class TestContentRefDeserialization:
    """Tests for content_ref deserialization after MongoDB round-trip.

    Verifies that the model_validator on ConversationTurn correctly coerces
    content_ref from a raw dict back to a ContentReference object.
    """

    def test_content_ref_coerced_from_dict_on_construction(self):
        """content_ref dict should be coerced to ContentReference when constructing from dict."""
        turn_data = {
            "turn_id": "turn-abc",
            "role": "user",
            "content": None,
            "representation": "compact",
            "content_ref": {
                "storage_type": "mongodb",
                "collection": "conversation_content",
                "document_id": "doc-789",
                "content_hash": "hash123",
                "created_at": datetime.now().isoformat(),
            },
            "timestamp": datetime.now().isoformat(),
        }

        turn = ConversationTurn(**turn_data)

        assert isinstance(turn.content_ref, ContentReference)
        assert turn.content_ref.storage_type == StorageType.MONGODB
        assert turn.content_ref.document_id == "doc-789"
        assert turn.content_ref.content_hash == "hash123"

    def test_content_ref_survives_full_room_memory_round_trip(self):
        """content_ref should survive serialize → deserialize through RoomMemory."""
        turn = ConversationTurn(
            turn_id="turn-roundtrip",
            role=TurnRole.AGENT,
            agent_name="TestAgent",
            content=None,
            representation=TurnRepresentation.COMPACT,
            content_ref=ContentReference(
                storage_type=StorageType.MONGODB,
                collection="conversation_content",
                document_id="doc-456",
                content_hash="abc123",
                created_at=datetime.now(),
            ),
            timestamp=datetime.now(),
            estimated_tokens_full=100,
            estimated_tokens_compact=20,
        )

        memory_content = MemoryContent(conversation_history=[turn])
        room_memory = RoomMemory(
            room_id="room-test",
            memory_id=str(uuid4()),
            memory_content=memory_content,
        )

        dumped = room_memory.model_dump(exclude_unset=True, mode="json")
        loaded = RoomMemory(**dumped)
        loaded_turn = loaded.get_conversation_history()[0]

        assert isinstance(loaded_turn.content_ref, ContentReference)
        assert loaded_turn.content_ref.storage_type == StorageType.MONGODB
        assert loaded_turn.content_ref.document_id == "doc-456"
        assert loaded_turn.content_ref.content_hash == "abc123"

    def test_to_context_string_shows_pointer_after_round_trip(self):
        """to_context_string should show the full pointer path, not generic fallback."""
        turn_data = {
            "turn_id": "turn-ctx",
            "role": "agent",
            "agent_name": "MyAgent",
            "content": None,
            "representation": "compact",
            "content_ref": {
                "storage_type": "mongodb",
                "collection": "conversation_content",
                "document_id": "doc-xyz",
                "created_at": datetime.now().isoformat(),
            },
            "timestamp": datetime.now().isoformat(),
        }

        turn = ConversationTurn(**turn_data)
        context_str = turn.to_context_string()

        assert "db/conversation_content/doc-xyz" in context_str
        assert "Content stored externally" not in context_str

    def test_content_ref_none_passes_through(self):
        """content_ref=None should not be affected by the validator."""
        turn = ConversationTurn(
            turn_id="turn-full",
            role=TurnRole.USER,
            content="Hello",
            representation=TurnRepresentation.FULL,
            content_ref=None,
            timestamp=datetime.now(),
        )

        assert turn.content_ref is None

    def test_content_ref_object_passes_through(self):
        """An already-constructed ContentReference should pass through unchanged."""
        ref = ContentReference(
            storage_type=StorageType.MONGODB,
            collection="conversation_content",
            document_id="doc-direct",
            created_at=datetime.now(),
        )

        turn = ConversationTurn(
            turn_id="turn-direct",
            role=TurnRole.USER,
            content=None,
            representation=TurnRepresentation.COMPACT,
            content_ref=ref,
            timestamp=datetime.now(),
        )

        assert turn.content_ref is ref
        assert isinstance(turn.content_ref, ContentReference)


class TestWriteBackPath:
    """Tests for compact_room_memory write-back correctness.

    Verifies that compacted history is written back to the same field
    that get_conversation_history() sourced it from.
    """

    @pytest.fixture
    def service(self):
        return bind_compaction_facade(CompactionService())

    @pytest.fixture(autouse=True)
    def mock_memory_search(self):
        with patch("services.memory_search_service.memory_search_service") as mock:
            mock.index_turn_for_search = AsyncMock(return_value=True)
            yield mock

    @pytest.mark.asyncio
    async def test_write_back_targets_conversation_history_field(
        self, service, mock_settings
    ):
        """Compaction should call compact_turns_bulk with correct turn data."""
        turns = [
            ConversationTurn(
                turn_id=f"turn-{i}",
                role=TurnRole.USER,
                content=f"Content {i}",
                representation=TurnRepresentation.FULL,
                estimated_tokens_full=50,
                estimated_tokens_compact=20,
                timestamp=datetime.now(),
            )
            for i in range(3)
        ]

        room_memory = RoomMemory(
            room_id="room-wb",
            memory_id=str(uuid4()),
            conversation_history=turns,
            memory_content=None,
        )

        mock_settings.compaction_preserve_recent = 0

        async def mock_upsert(*args, **kwargs):
            return "doc-id"

        mock_compact = AsyncMock(return_value=True)

        with patch.object(
            service.db_service,
            "get_room_memory_by_room_id",
            return_value=room_memory,
        ):
            with patch.object(
                service.db_service,
                "compact_turns_bulk",
                mock_compact,
            ):
                with patch.object(
                    service.content_storage, "upsert_full_content", mock_upsert
                ):
                    await service.compact_room_memory("room-wb")

        mock_compact.assert_awaited_once()
        compacted_turns = mock_compact.call_args[0][1]
        assert len(compacted_turns) == 3
        assert all(t["turn_id"].startswith("turn-") for t in compacted_turns)

    @pytest.mark.asyncio
    async def test_write_back_targets_memory_content_field(
        self, service, mock_settings
    ):
        """Compaction should call compact_turns_bulk for memory_content-sourced turns."""
        turns = [
            ConversationTurn(
                turn_id=f"turn-{i}",
                role=TurnRole.USER,
                content=f"Content {i}",
                representation=TurnRepresentation.FULL,
                estimated_tokens_full=50,
                estimated_tokens_compact=20,
                timestamp=datetime.now(),
            )
            for i in range(3)
        ]

        memory_content = MemoryContent(conversation_history=turns)
        room_memory = RoomMemory(
            room_id="room-mc",
            memory_id=str(uuid4()),
            memory_content=memory_content,
        )

        mock_settings.compaction_preserve_recent = 0

        async def mock_upsert(*args, **kwargs):
            return "doc-id"

        mock_compact = AsyncMock(return_value=True)

        with patch.object(
            service.db_service,
            "get_room_memory_by_room_id",
            return_value=room_memory,
        ):
            with patch.object(
                service.db_service,
                "compact_turns_bulk",
                mock_compact,
            ):
                with patch.object(
                    service.content_storage, "upsert_full_content", mock_upsert
                ):
                    await service.compact_room_memory("room-mc")

        mock_compact.assert_awaited_once()
        compacted_turns = mock_compact.call_args[0][1]
        assert len(compacted_turns) == 3


# =============================================================================
# Test: Compact turn eviction by window trimming in add_turn_to_history
# =============================================================================


class TestCompactTurnEviction:
    """Verify that compact turns produce meaningful summaries when evicted."""

    def test_compact_turn_eviction_uses_to_context_string(self):
        """When add_turn_to_history evicts a COMPACT turn from the window,
        the summary should contain the brief_summary + pointer, not
        '[content unavailable]'.
        """
        from common.utils.context_utils import add_turn_to_history, MAX_HISTORY_TURNS

        memory = MemoryContent()

        compact_turn = ConversationTurn(
            role=TurnRole.AGENT,
            agent_name="CodeAgent",
            content=None,
            representation=TurnRepresentation.COMPACT,
            brief_summary="Implemented login flow",
            content_ref=ContentReference(
                storage_type=StorageType.MONGODB,
                collection="conversation_content",
                document_id="abc123",
            ),
            estimated_tokens_full=500,
            estimated_tokens_compact=20,
        )

        memory.conversation_history = [compact_turn]

        for i in range(MAX_HISTORY_TURNS):
            add_turn_to_history(
                memory_content=memory,
                role="user",
                content=f"Message {i}",
            )

        assert memory.summary is not None
        assert "[content unavailable]" not in memory.summary
        assert "Implemented login flow" in memory.summary
