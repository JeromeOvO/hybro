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

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from models.compaction import (
    CompactionResult,
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
    with patch("services.content_storage_service.settings") as mock:
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


class TestContentStorageService:
    """Tests for ContentStorageService."""

    @pytest.fixture
    def service(self):
        """Create a ContentStorageService instance."""
        return ContentStorageService()

    @pytest.mark.asyncio
    async def test_upsert_full_content_new_document(
        self, service, mock_content_settings
    ):
        """Upsert should create new document and return ID."""
        mock_collection = AsyncMock()
        mock_collection.update_one = AsyncMock(
            return_value=MagicMock(upserted_id="new-doc-id-123")
        )

        with patch.object(service, "collection", mock_collection):
            doc_id = await service.upsert_full_content(
                room_id="room-123",
                turn_id="turn-456",
                content="Test content",
                content_type="text",
            )

        assert doc_id == "new-doc-id-123"
        mock_collection.update_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_upsert_full_content_existing_document(
        self, service, mock_content_settings
    ):
        """Upsert should return existing document ID if already exists."""
        from bson import ObjectId

        existing_id = ObjectId()
        mock_collection = AsyncMock()
        mock_collection.update_one = AsyncMock(
            return_value=MagicMock(upserted_id=None)
        )
        mock_collection.find_one = AsyncMock(return_value={"_id": existing_id})

        with patch.object(service, "collection", mock_collection):
            doc_id = await service.upsert_full_content(
                room_id="room-123",
                turn_id="turn-456",
                content="Test content",
                content_type="text",
            )

        assert doc_id == str(existing_id)

    @pytest.mark.asyncio
    async def test_get_content_by_document_id(self, service):
        """Should retrieve content by document ID."""
        from bson import ObjectId

        doc_id = str(ObjectId())
        mock_collection = AsyncMock()
        mock_collection.find_one = AsyncMock(
            return_value={"_id": ObjectId(doc_id), "content": "Retrieved content"}
        )

        with patch.object(service, "collection", mock_collection):
            content = await service.get_content_by_document_id(doc_id)

        assert content == "Retrieved content"

    @pytest.mark.asyncio
    async def test_get_content_by_document_id_not_found(self, service):
        """Should return None if document not found."""
        from bson import ObjectId

        mock_collection = AsyncMock()
        mock_collection.find_one = AsyncMock(return_value=None)

        with patch.object(service, "collection", mock_collection):
            content = await service.get_content_by_document_id(str(ObjectId()))

        assert content is None

    @pytest.mark.asyncio
    async def test_expand_content_reference_mongodb(self, service):
        """Should expand MongoDB content reference."""
        from bson import ObjectId

        doc_id = str(ObjectId())
        content_ref = ContentReference(
            storage_type=StorageType.MONGODB,
            collection="conversation_content",
            document_id=doc_id,
            created_at=datetime.now(),
        )

        mock_collection = AsyncMock()
        mock_collection.find_one = AsyncMock(
            return_value={"_id": ObjectId(doc_id), "content": "Expanded content"}
        )

        with patch.object(service, "collection", mock_collection):
            content = await service.expand_content_reference(content_ref, "turn-123")

        assert content == "Expanded content"

    @pytest.mark.asyncio
    async def test_expand_content_reference_not_found_raises_error(self, service):
        """Should raise ContentExpiredError if content not found."""
        from bson import ObjectId

        doc_id = str(ObjectId())
        content_ref = ContentReference(
            storage_type=StorageType.MONGODB,
            collection="conversation_content",
            document_id=doc_id,
            created_at=datetime.now(),
        )

        mock_collection = AsyncMock()
        mock_collection.find_one = AsyncMock(return_value=None)

        with patch.object(service, "collection", mock_collection):
            with pytest.raises(ContentExpiredError) as exc_info:
                await service.expand_content_reference(content_ref, "turn-123")

        assert exc_info.value.turn_id == "turn-123"
        assert exc_info.value.document_id == doc_id

    @pytest.mark.asyncio
    async def test_expand_content_reference_s3_not_implemented(self, service):
        """Should raise NotImplementedError for S3 storage."""
        content_ref = ContentReference(
            storage_type=StorageType.S3,
            s3_bucket="test-bucket",
            s3_key="test-key",
            created_at=datetime.now(),
        )

        with pytest.raises(NotImplementedError):
            await service.expand_content_reference(content_ref, "turn-123")


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
        return CompactionService()

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


# =============================================================================
# Round-Trip Tests
# =============================================================================


class TestCompactionRoundTrip:
    """Tests for complete compaction -> expansion round-trip."""

    @pytest.fixture
    def service(self):
        """Create a CompactionService instance."""
        return CompactionService()

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
        return CompactionService()

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
        return CompactionService()

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
        return CompactionService()

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
