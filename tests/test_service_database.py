"""
Unit tests for DatabaseService (database_service.py).

Tests cover:
- _build_visibility_filter: public-only vs user-specific queries
- get_all_visible_agents: delegation with correct filter
- get_all_active_agents: delegation with correct filter
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.database_service import DatabaseService


@pytest.fixture
def db_svc():
    """Create a DatabaseService with mocked Mongo driver."""
    svc = object.__new__(DatabaseService)
    svc.mongo = MagicMock()
    return svc


# =============================================================================
# _build_visibility_filter Tests
# =============================================================================


class TestBuildVisibilityFilter:
    """Tests for MongoDB visibility query building."""

    def test_public_only_when_no_user(self, db_svc):
        """Without user_id, only return public agents."""
        f = db_svc._build_visibility_filter(None)
        assert "$or" in f
        conditions = f["$or"]
        assert {"is_public": True} in conditions
        assert {"is_public": {"$exists": False}} in conditions
        # Should NOT contain provider_id condition
        assert all("provider_id" not in c for c in conditions)

    def test_includes_user_private_when_user_provided(self, db_svc):
        """With user_id, include that user's private agents."""
        f = db_svc._build_visibility_filter("user-001")
        assert "$or" in f
        conditions = f["$or"]
        assert {"provider_id": "user-001"} in conditions
        assert {"is_public": True} in conditions
        assert {"is_public": {"$exists": False}} in conditions

    def test_empty_string_user_id_treated_as_no_user(self, db_svc):
        """Empty string user_id should behave like None (public only)."""
        f = db_svc._build_visibility_filter("")
        conditions = f["$or"]
        assert all("provider_id" not in c for c in conditions)


# =============================================================================
# get_all_visible_agents Tests
# =============================================================================


class TestGetAllVisibleAgents:
    """Tests for visible agents retrieval."""

    @pytest.mark.asyncio
    async def test_passes_visibility_filter_to_mongo(self, db_svc):
        db_svc.mongo.get_agents_with_conditions = AsyncMock(return_value=[])
        await db_svc.get_all_visible_agents(user_id="user-001")

        db_svc.mongo.get_agents_with_conditions.assert_called_once()
        query = db_svc.mongo.get_agents_with_conditions.call_args[0][0]
        assert {"provider_id": "user-001"} in query["$or"]

    @pytest.mark.asyncio
    async def test_public_only_without_user(self, db_svc):
        db_svc.mongo.get_agents_with_conditions = AsyncMock(return_value=[])
        await db_svc.get_all_visible_agents()

        query = db_svc.mongo.get_agents_with_conditions.call_args[0][0]
        conditions = query["$or"]
        assert all("provider_id" not in c for c in conditions)


# =============================================================================
# Room Queries Tests
# =============================================================================


class TestRoomQueries:
    """Tests for room query delegation to mongo."""

    @pytest.mark.asyncio
    async def test_get_rooms_by_room_owner_id_delegates_to_mongo(self, db_svc):
        sentinel = [MagicMock(), MagicMock()]
        db_svc.mongo.get_rooms_by_room_owner_id = AsyncMock(return_value=sentinel)

        result = await db_svc.get_rooms_by_room_owner_id("owner-1")

        db_svc.mongo.get_rooms_by_room_owner_id.assert_awaited_once_with("owner-1")
        assert result is sentinel

    @pytest.mark.asyncio
    async def test_get_rooms_by_room_owner_id_empty(self, db_svc):
        db_svc.mongo.get_rooms_by_room_owner_id = AsyncMock(return_value=[])

        result = await db_svc.get_rooms_by_room_owner_id("owner-2")

        assert result == []

    @pytest.mark.asyncio
    async def test_get_room_by_room_id_returns_none_when_missing(self, db_svc):
        db_svc.mongo.get_room_by_room_id = AsyncMock(return_value=None)

        result = await db_svc.get_room_by_room_id("nonexistent")

        db_svc.mongo.get_room_by_room_id.assert_awaited_once_with("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_room_by_room_id_returns_none_on_error(self, db_svc):
        db_svc.mongo.get_room_by_room_id = AsyncMock(
            side_effect=RuntimeError("connection lost")
        )

        result = await db_svc.get_room_by_room_id("room-x")

        assert result is None


# =============================================================================
# Idempotency & CAS Tests
# =============================================================================


class TestIdempotencyAndCAS:
    """Tests for idempotent state updates and compare-and-swap operations."""

    @pytest.mark.asyncio
    async def test_update_last_notified_state_returns_true_on_new_state(self, db_svc):
        db_svc.mongo.update_last_notified_state = AsyncMock(return_value=True)

        result = await db_svc.update_last_notified_state("msg-1", "working")

        db_svc.mongo.update_last_notified_state.assert_awaited_once_with(
            "msg-1", "working"
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_update_last_notified_state_returns_false_on_duplicate(self, db_svc):
        db_svc.mongo.update_last_notified_state = AsyncMock(return_value=False)

        result = await db_svc.update_last_notified_state("msg-1", "working")

        assert result is False

    @pytest.mark.asyncio
    async def test_reset_last_notified_state(self, db_svc):
        mock_result = MagicMock(modified_count=1)
        db_svc.mongo.db.room_agent_messages.update_one = AsyncMock(
            return_value=mock_result
        )

        result = await db_svc.reset_last_notified_state("msg-2")

        db_svc.mongo.db.room_agent_messages.update_one.assert_awaited_once()
        assert result is True

    @pytest.mark.asyncio
    async def test_cas_update_hitl_request_succeeds_on_matching_version(self, db_svc):
        mock_result = MagicMock(modified_count=1)
        db_svc.mongo.db.hitl_requests.update_one = AsyncMock(
            return_value=mock_result
        )

        result = await db_svc.cas_update_hitl_request(
            "req-1", "pending", status="processing"
        )

        db_svc.mongo.db.hitl_requests.update_one.assert_awaited_once_with(
            {"request_id": "req-1", "status": "pending"},
            {"$set": {"status": "processing"}},
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_cas_update_hitl_request_fails_on_version_mismatch(self, db_svc):
        mock_result = MagicMock(modified_count=0)
        db_svc.mongo.db.hitl_requests.update_one = AsyncMock(
            return_value=mock_result
        )

        result = await db_svc.cas_update_hitl_request(
            "req-1", "pending", status="processing"
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_claim_hitl_group_routing_claims_group_leader_once(self, db_svc):
        mock_result = MagicMock(modified_count=1)
        db_svc.mongo.db.hitl_requests.update_one = AsyncMock(
            return_value=mock_result
        )

        result = await db_svc.claim_hitl_group_routing("group-1", "claim-1")

        db_svc.mongo.db.hitl_requests.update_one.assert_awaited_once()
        query, update = db_svc.mongo.db.hitl_requests.update_one.await_args.args
        assert query == {
            "group_id": "group-1",
            "group_index": 0,
            "group_routing_claim_id": {"$exists": False},
        }
        assert update["$set"]["group_routing_claim_id"] == "claim-1"
        assert "group_routing_claimed_at" in update["$set"]
        assert result is True

    @pytest.mark.asyncio
    async def test_release_hitl_group_routing_clears_matching_claim(self, db_svc):
        mock_result = MagicMock(modified_count=1)
        db_svc.mongo.db.hitl_requests.update_one = AsyncMock(
            return_value=mock_result
        )

        result = await db_svc.release_hitl_group_routing("group-1", "claim-1")

        db_svc.mongo.db.hitl_requests.update_one.assert_awaited_once_with(
            {"group_id": "group-1", "group_routing_claim_id": "claim-1"},
            {
                "$unset": {
                    "group_routing_claim_id": "",
                    "group_routing_claimed_at": "",
                }
            },
        )
        assert result is True


# =============================================================================
# Agent CRUD Tests
# =============================================================================


class TestAgentCRUD:
    """Tests for agent create / read / update / delete delegation."""

    @pytest.mark.asyncio
    async def test_add_agent_success(self, db_svc):
        agent = MagicMock()
        agent.agent_id = "agent-1"
        agent.agent_card.description = "A helpful agent"

        db_svc.mongo.get_agent_by_agent_id = AsyncMock(return_value=None)
        db_svc.mongo.add_agent = AsyncMock(return_value="inserted-id")
        db_svc.ai_service = MagicMock()
        db_svc.ai_service.get_embedding = AsyncMock(return_value=[0.1, 0.2])
        db_svc.pinecone = MagicMock()

        result = await db_svc.add_agent(agent)

        assert result is True
        db_svc.mongo.add_agent.assert_awaited_once_with(agent)
        db_svc.pinecone.upsert.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_agent_by_agent_id(self, db_svc):
        db_svc.mongo.delete_agent_by_agent_id = AsyncMock(return_value=True)
        db_svc.pinecone = MagicMock()

        result = await db_svc.delete_agent_by_agent_id("agent-1")

        assert result is True
        db_svc.mongo.delete_agent_by_agent_id.assert_awaited_once_with("agent-1")
        db_svc.pinecone.delete.assert_called_once_with(["agent-1"])

    @pytest.mark.asyncio
    async def test_update_agent_by_agent_id(self, db_svc):
        agent = MagicMock()
        agent.agent_card.description = "updated description"

        db_svc.mongo.get_agent_by_agent_id = AsyncMock(return_value=MagicMock())
        db_svc.mongo.update_agent_by_agent_id = AsyncMock(return_value=True)
        db_svc.ai_service = MagicMock()
        db_svc.ai_service.get_embedding = AsyncMock(return_value=[0.3, 0.4])
        db_svc.pinecone = MagicMock()

        result = await db_svc.update_agent_by_agent_id("agent-1", agent)

        assert result is True
        db_svc.mongo.update_agent_by_agent_id.assert_awaited_once_with("agent-1", agent)
        db_svc.pinecone.upsert.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_agent_by_agent_id_returns_none_when_missing(self, db_svc):
        db_svc.mongo.get_agent_by_agent_id = AsyncMock(return_value=None)

        result = await db_svc.get_agent_by_agent_id("nonexistent")

        db_svc.mongo.get_agent_by_agent_id.assert_awaited_once_with("nonexistent")
        assert result is None


# =============================================================================
# Atomic Memory Ops Tests
# =============================================================================


class TestAtomicMemoryOps:
    """Tests for atomic room-memory mutation delegation."""

    @pytest.mark.asyncio
    async def test_push_conversation_turn_success(self, db_svc):
        sentinel = (True, True)
        db_svc.mongo.push_conversation_turn = AsyncMock(return_value=sentinel)

        result = await db_svc.push_conversation_turn("room-1", {"role": "user"})

        db_svc.mongo.push_conversation_turn.assert_awaited_once_with(
            "room-1", {"role": "user"}
        )
        assert result is sentinel

    @pytest.mark.asyncio
    async def test_push_conversation_turn_error_path(self, db_svc):
        db_svc.mongo.push_conversation_turn = AsyncMock(
            side_effect=RuntimeError("write conflict")
        )

        result = await db_svc.push_conversation_turn("room-1", {"role": "user"})

        assert result == (False, False)

    @pytest.mark.asyncio
    async def test_push_and_trim_conversation_turn(self, db_svc):
        sentinel = (True, True)
        db_svc.mongo.push_and_trim_conversation_turn = AsyncMock(
            return_value=sentinel
        )

        result = await db_svc.push_and_trim_conversation_turn(
            "room-1", {"role": "assistant"}, 50, "summary stub", 4000
        )

        db_svc.mongo.push_and_trim_conversation_turn.assert_awaited_once_with(
            "room-1", {"role": "assistant"}, 50, "summary stub", 4000
        )
        assert result is sentinel

    @pytest.mark.asyncio
    async def test_update_room_summary_atomic(self, db_svc):
        db_svc.mongo.update_room_summary_atomic = AsyncMock(return_value=True)
        summary = {"text": "conversation so far"}

        result = await db_svc.update_room_summary_atomic(
            "room-1", summary, None, 50
        )

        db_svc.mongo.update_room_summary_atomic.assert_awaited_once_with(
            "room-1", summary, None, 50
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_compact_turns_bulk(self, db_svc):
        db_svc.mongo.compact_turns_bulk = AsyncMock(return_value=True)
        compacted = [{"turn_id": "t1", "compact": True}]

        result = await db_svc.compact_turns_bulk("room-1", compacted)

        db_svc.mongo.compact_turns_bulk.assert_awaited_once_with("room-1", compacted)
        assert result is True


# =============================================================================
# Continuation Tests
# =============================================================================


class TestContinuation:
    """Tests for continuation save / get-and-clear / has delegation."""

    @pytest.mark.asyncio
    async def test_save_continuation_on_message(self, db_svc):
        db_svc.mongo.save_continuation_on_message = AsyncMock(return_value=True)
        data = {"queue": "items", "index": 3}

        result = await db_svc.save_continuation_on_message("msg-1", data)

        db_svc.mongo.save_continuation_on_message.assert_awaited_once_with(
            "msg-1", data
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_get_and_clear_continuation_on_message(self, db_svc):
        stored = {"queue": "items", "index": 3}
        db_svc.mongo.get_and_clear_continuation_on_message = AsyncMock(
            return_value=stored
        )

        result = await db_svc.get_and_clear_continuation_on_message("msg-1")

        db_svc.mongo.get_and_clear_continuation_on_message.assert_awaited_once_with(
            "msg-1"
        )
        assert result is stored

    @pytest.mark.asyncio
    async def test_has_continuation_on_message(self, db_svc):
        db_svc.mongo.has_continuation_on_message = AsyncMock(return_value=True)

        result = await db_svc.has_continuation_on_message("msg-1")

        db_svc.mongo.has_continuation_on_message.assert_awaited_once_with("msg-1")
        assert result is True


# =============================================================================
# Atomic Artifact Accumulation Tests
# =============================================================================


class TestAccumulateArtifactOnMessage:
    """Tests for atomic artifact accumulation (accumulate_artifact_on_message)."""

    @pytest.mark.asyncio
    async def test_missing_artifact_id_pushes_new_artifact(self, db_svc):
        """Artifact without artifactId is pushed as new."""
        mock_result = MagicMock(modified_count=1)
        db_svc.mongo.room_agent_messages_collection = MagicMock()
        db_svc.mongo.room_agent_messages_collection.update_one = AsyncMock(
            return_value=mock_result
        )

        artifact = {"parts": [{"text": "hello"}]}
        result = await db_svc.accumulate_artifact_on_message("msg-1", artifact)

        assert result is True
        call_args = db_svc.mongo.room_agent_messages_collection.update_one.call_args
        update_doc = call_args[0][1]
        assert "$push" in update_doc
        assert "message_content.message_task.artifacts" in update_doc["$push"]

    @pytest.mark.asyncio
    async def test_append_false_replaces_existing_artifact(self, db_svc):
        """append=False replaces artifact with matching artifactId."""
        mock_result = MagicMock(modified_count=1)
        db_svc.mongo.room_agent_messages_collection = MagicMock()
        db_svc.mongo.room_agent_messages_collection.update_one = AsyncMock(
            return_value=mock_result
        )

        artifact = {"artifactId": "art-1", "parts": [{"text": "new content"}]}
        result = await db_svc.accumulate_artifact_on_message(
            "msg-1", artifact, append=False
        )

        assert result is True
        call_args = db_svc.mongo.room_agent_messages_collection.update_one.call_args
        update_doc = call_args[0][1]
        assert "$set" in update_doc
        assert "message_content.message_task.artifacts.$" in update_doc["$set"]

    @pytest.mark.asyncio
    async def test_append_false_inserts_when_not_found(self, db_svc):
        """append=False inserts new artifact if artifactId not found."""
        mock_result_not_found = MagicMock(modified_count=0)
        mock_result_inserted = MagicMock(modified_count=1)
        db_svc.mongo.room_agent_messages_collection = MagicMock()
        db_svc.mongo.room_agent_messages_collection.update_one = AsyncMock(
            side_effect=[mock_result_not_found, mock_result_inserted]
        )

        artifact = {"artifactId": "art-new", "parts": [{"text": "content"}]}
        result = await db_svc.accumulate_artifact_on_message(
            "msg-1", artifact, append=False
        )

        assert result is True
        assert db_svc.mongo.room_agent_messages_collection.update_one.await_count == 2
        second_call = db_svc.mongo.room_agent_messages_collection.update_one.call_args_list[1]
        update_doc = second_call[0][1]
        assert "$push" in update_doc

    @pytest.mark.asyncio
    async def test_append_true_extends_parts_atomically(self, db_svc):
        """append=True extends parts of existing artifact."""
        mock_result = MagicMock(modified_count=1)
        db_svc.mongo.room_agent_messages_collection = MagicMock()
        db_svc.mongo.room_agent_messages_collection.update_one = AsyncMock(
            return_value=mock_result
        )

        artifact = {"artifactId": "art-1", "parts": [{"text": " more"}]}
        result = await db_svc.accumulate_artifact_on_message(
            "msg-1", artifact, append=True
        )

        assert result is True
        call_args = db_svc.mongo.room_agent_messages_collection.update_one.call_args
        filter_doc = call_args[0][0]
        assert "message_content.message_task.artifacts" in filter_doc
        assert "$elemMatch" in filter_doc["message_content.message_task.artifacts"]

    @pytest.mark.asyncio
    async def test_append_true_with_text_uses_pipeline_for_concat(self, db_svc):
        """append=True with text uses aggregation pipeline for atomic concat."""
        mock_result = MagicMock(modified_count=1)
        db_svc.mongo.room_agent_messages_collection = MagicMock()
        db_svc.mongo.room_agent_messages_collection.update_one = AsyncMock(
            return_value=mock_result
        )

        artifact = {"artifactId": "art-1", "parts": [{"text": " appended"}]}
        result = await db_svc.accumulate_artifact_on_message(
            "msg-1", artifact, append=True
        )

        assert result is True
        call_args = db_svc.mongo.room_agent_messages_collection.update_one.call_args
        update_doc = call_args[0][1]
        assert isinstance(update_doc, list)
        assert "$set" in update_doc[0]
        set_stage = update_doc[0]["$set"]
        assert "message_content.message_text" in set_stage
        assert "$concat" in set_stage["message_content.message_text"]

    @pytest.mark.asyncio
    async def test_append_true_inserts_when_artifact_not_found(self, db_svc):
        """append=True inserts new artifact if artifactId doesn't exist."""
        mock_result_not_found = MagicMock(modified_count=0)
        mock_result_inserted = MagicMock(modified_count=1)
        db_svc.mongo.room_agent_messages_collection = MagicMock()
        db_svc.mongo.room_agent_messages_collection.update_one = AsyncMock(
            side_effect=[mock_result_not_found, mock_result_inserted]
        )

        artifact = {"artifactId": "art-new", "parts": [{"text": "first chunk"}]}
        result = await db_svc.accumulate_artifact_on_message(
            "msg-1", artifact, append=True
        )

        assert result is True
        assert db_svc.mongo.room_agent_messages_collection.update_one.await_count == 2

    @pytest.mark.asyncio
    async def test_empty_parts_with_append_returns_false(self, db_svc):
        """append=True with empty parts returns False."""
        db_svc.mongo.room_agent_messages_collection = MagicMock()
        db_svc.mongo.room_agent_messages_collection.update_one = AsyncMock()

        artifact = {"artifactId": "art-1", "parts": []}
        result = await db_svc.accumulate_artifact_on_message(
            "msg-1", artifact, append=True
        )

        assert result is False
        db_svc.mongo.room_agent_messages_collection.update_one.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_terminal_state_filter_applied(self, db_svc):
        """Filter excludes documents in terminal states."""
        mock_result = MagicMock(modified_count=1)
        db_svc.mongo.room_agent_messages_collection = MagicMock()
        db_svc.mongo.room_agent_messages_collection.update_one = AsyncMock(
            return_value=mock_result
        )

        artifact = {"artifactId": "art-1", "parts": [{"text": "x"}]}
        await db_svc.accumulate_artifact_on_message("msg-1", artifact)

        call_args = db_svc.mongo.room_agent_messages_collection.update_one.call_args
        filter_doc = call_args[0][0]
        assert "message_content.message_task.status.state" in filter_doc
        assert "$nin" in filter_doc["message_content.message_task.status.state"]

    @pytest.mark.asyncio
    async def test_sets_working_state(self, db_svc):
        """Accumulation sets task state to 'working'."""
        mock_result = MagicMock(modified_count=1)
        db_svc.mongo.room_agent_messages_collection = MagicMock()
        db_svc.mongo.room_agent_messages_collection.update_one = AsyncMock(
            return_value=mock_result
        )

        artifact = {"artifactId": "art-1", "parts": [{"text": "x"}]}
        await db_svc.accumulate_artifact_on_message("msg-1", artifact, append=False)

        call_args = db_svc.mongo.room_agent_messages_collection.update_one.call_args
        update_doc = call_args[0][1]
        assert update_doc["$set"]["message_content.message_task.status.state"] == "working"

    @pytest.mark.asyncio
    async def test_handles_artifact_id_snake_case(self, db_svc):
        """Handles artifact_id (snake_case) as well as artifactId."""
        mock_result = MagicMock(modified_count=1)
        db_svc.mongo.room_agent_messages_collection = MagicMock()
        db_svc.mongo.room_agent_messages_collection.update_one = AsyncMock(
            return_value=mock_result
        )

        artifact = {"artifact_id": "art-snake", "parts": [{"text": "x"}]}
        result = await db_svc.accumulate_artifact_on_message("msg-1", artifact)

        assert result is True
        call_args = db_svc.mongo.room_agent_messages_collection.update_one.call_args
        filter_doc = call_args[0][0]
        elem_match = filter_doc["message_content.message_task.artifacts"]["$elemMatch"]
        or_conditions = elem_match["$or"]
        assert {"artifact_id": "art-snake"} in or_conditions

    @pytest.mark.asyncio
    async def test_exception_returns_false(self, db_svc):
        """Exception during update returns False."""
        db_svc.mongo.room_agent_messages_collection = MagicMock()
        db_svc.mongo.room_agent_messages_collection.update_one = AsyncMock(
            side_effect=RuntimeError("connection lost")
        )

        artifact = {"artifactId": "art-1", "parts": [{"text": "x"}]}
        result = await db_svc.accumulate_artifact_on_message("msg-1", artifact)

        assert result is False

    @pytest.mark.asyncio
    async def test_extracts_text_from_nested_root(self, db_svc):
        """Extracts text from part.root.text structure."""
        mock_result = MagicMock(modified_count=1)
        db_svc.mongo.room_agent_messages_collection = MagicMock()
        db_svc.mongo.room_agent_messages_collection.update_one = AsyncMock(
            return_value=mock_result
        )

        artifact = {
            "artifactId": "art-1",
            "parts": [{"root": {"text": "nested text"}}],
        }
        await db_svc.accumulate_artifact_on_message("msg-1", artifact, append=False)

        call_args = db_svc.mongo.room_agent_messages_collection.update_one.call_args
        update_doc = call_args[0][1]
        assert update_doc["$set"]["message_content.message_text"] == "nested text"


# =============================================================================
# query_similar_agents_with_scores Tests
# =============================================================================


class TestQuerySimilarAgentsWithScores:
    """Tests for query_similar_agents_with_scores method."""

    @pytest.mark.asyncio
    async def test_returns_agents_with_scores(self, db_svc):
        """Test that scores are preserved and paired correctly with agents."""
        from models.agent import AgentStatus

        # Setup mocks
        db_svc.ai_service = MagicMock()
        db_svc.ai_service.get_embedding = AsyncMock(return_value=[0.1, 0.2, 0.3])

        # Mock Pinecone results with scores
        mock_matches = [
            MagicMock(id="agent-1", score=0.95),
            MagicMock(id="agent-2", score=0.85),
            MagicMock(id="agent-3", score=0.75),
        ]
        mock_results = MagicMock(matches=mock_matches)
        db_svc.pinecone = MagicMock()
        db_svc.pinecone.query = MagicMock(return_value=mock_results)

        # Mock MongoDB results
        agent1 = MagicMock()
        agent1.agent_id = "agent-1"
        agent1.agent_status = AgentStatus.active
        agent2 = MagicMock()
        agent2.agent_id = "agent-2"
        agent2.agent_status = AgentStatus.active
        agent3 = MagicMock()
        agent3.agent_id = "agent-3"
        agent3.agent_status = AgentStatus.active

        db_svc.mongo.get_agents_with_conditions = AsyncMock(
            return_value=[agent1, agent2, agent3]
        )

        # Call method
        result = await db_svc.query_similar_agents_with_scores(
            query_text="test query", count=3
        )

        # Verify results
        assert len(result) == 3
        assert result[0] == (agent1, 0.95)
        assert result[1] == (agent2, 0.85)
        assert result[2] == (agent3, 0.75)

    @pytest.mark.asyncio
    async def test_maintains_pinecone_score_ordering(self, db_svc):
        """Test that Pinecone score ordering is maintained in results."""
        from models.agent import AgentStatus

        db_svc.ai_service = MagicMock()
        db_svc.ai_service.get_embedding = AsyncMock(return_value=[0.1, 0.2])

        # Pinecone returns in descending score order
        mock_matches = [
            {"id": "agent-3", "score": 0.90},
            {"id": "agent-1", "score": 0.80},
            {"id": "agent-2", "score": 0.70},
        ]
        mock_results = MagicMock(matches=mock_matches)
        db_svc.pinecone = MagicMock()
        db_svc.pinecone.query = MagicMock(return_value=mock_results)

        # MongoDB returns in arbitrary order
        agent1 = MagicMock()
        agent1.agent_id = "agent-1"
        agent1.agent_status = AgentStatus.active
        agent2 = MagicMock()
        agent2.agent_id = "agent-2"
        agent2.agent_status = AgentStatus.active
        agent3 = MagicMock()
        agent3.agent_id = "agent-3"
        agent3.agent_status = AgentStatus.active

        db_svc.mongo.get_agents_with_conditions = AsyncMock(
            return_value=[agent1, agent2, agent3]
        )

        result = await db_svc.query_similar_agents_with_scores("test", count=3)

        # Should be sorted by score descending
        assert result[0][0].agent_id == "agent-3"
        assert result[0][1] == 0.90
        assert result[1][0].agent_id == "agent-1"
        assert result[1][1] == 0.80
        assert result[2][0].agent_id == "agent-2"
        assert result[2][1] == 0.70

    @pytest.mark.asyncio
    async def test_active_only_filtering(self, db_svc):
        """Test that active_only filtering works correctly."""
        from models.agent import AgentStatus

        db_svc.ai_service = MagicMock()
        db_svc.ai_service.get_embedding = AsyncMock(return_value=[0.1])

        mock_matches = [
            {"id": "agent-1", "score": 0.9},
            {"id": "agent-2", "score": 0.8},
        ]
        db_svc.pinecone = MagicMock()
        db_svc.pinecone.query = MagicMock(return_value=MagicMock(matches=mock_matches))

        # One active, one inactive
        agent1 = MagicMock()
        agent1.agent_id = "agent-1"
        agent1.agent_status = AgentStatus.active
        agent2 = MagicMock()
        agent2.agent_id = "agent-2"
        agent2.agent_status = AgentStatus.inactive

        db_svc.mongo.get_agents_with_conditions = AsyncMock(
            return_value=[agent1, agent2]
        )

        result = await db_svc.query_similar_agents_with_scores(
            "test", count=2, active_only=True
        )

        # Should only return active agent
        assert len(result) == 1
        assert result[0][0].agent_id == "agent-1"
        assert result[0][1] == 0.9

    @pytest.mark.asyncio
    async def test_excluded_agent_ids_filter(self, db_svc):
        """Test that excluded_agent_ids filtering works correctly."""
        from models.agent import AgentStatus

        db_svc.ai_service = MagicMock()
        db_svc.ai_service.get_embedding = AsyncMock(return_value=[0.1])

        mock_matches = [{"id": "agent-2", "score": 0.8}]
        db_svc.pinecone = MagicMock()
        db_svc.pinecone.query = MagicMock(return_value=MagicMock(matches=mock_matches))

        agent2 = MagicMock()
        agent2.agent_id = "agent-2"
        agent2.agent_status = AgentStatus.active

        db_svc.mongo.get_agents_with_conditions = AsyncMock(return_value=[agent2])

        result = await db_svc.query_similar_agents_with_scores(
            "test", count=2, excluded_agent_ids={"agent-1"}
        )

        # Verify Pinecone was called with $nin filter
        call_args = db_svc.pinecone.query.call_args
        filter_arg = call_args[1]["filter"]
        assert filter_arg == {"agent_id": {"$nin": ["agent-1"]}}

        # Should only return agent-2
        assert len(result) == 1
        assert result[0][0].agent_id == "agent-2"

    @pytest.mark.asyncio
    async def test_empty_results(self, db_svc):
        """Test handling of empty Pinecone results."""
        db_svc.ai_service = MagicMock()
        db_svc.ai_service.get_embedding = AsyncMock(return_value=[0.1])

        # Empty matches
        db_svc.pinecone = MagicMock()
        db_svc.pinecone.query = MagicMock(return_value=MagicMock(matches=[]))

        result = await db_svc.query_similar_agents_with_scores("test", count=5)

        assert result == []

    @pytest.mark.asyncio
    async def test_none_pinecone_results(self, db_svc):
        """Test handling of None Pinecone results."""
        db_svc.ai_service = MagicMock()
        db_svc.ai_service.get_embedding = AsyncMock(return_value=[0.1])

        db_svc.pinecone = MagicMock()
        db_svc.pinecone.query = MagicMock(return_value=None)

        result = await db_svc.query_similar_agents_with_scores("test", count=5)

        assert result == []

    @pytest.mark.asyncio
    async def test_top_k_calculation_with_active_only(self, db_svc):
        """Test that top_k is calculated correctly when active_only=True."""
        db_svc.ai_service = MagicMock()
        db_svc.ai_service.get_embedding = AsyncMock(return_value=[0.1])

        db_svc.pinecone = MagicMock()
        db_svc.pinecone.query = MagicMock(return_value=MagicMock(matches=[]))

        await db_svc.query_similar_agents_with_scores(
            "test", count=5, active_only=True
        )

        # top_k should be max(5 * 3, 15) = 15
        call_args = db_svc.pinecone.query.call_args
        assert call_args[1]["top_k"] == 15

    @pytest.mark.asyncio
    async def test_top_k_calculation_without_active_only(self, db_svc):
        """Test that top_k equals count when active_only=False."""
        db_svc.ai_service = MagicMock()
        db_svc.ai_service.get_embedding = AsyncMock(return_value=[0.1])

        db_svc.pinecone = MagicMock()
        db_svc.pinecone.query = MagicMock(return_value=MagicMock(matches=[]))

        await db_svc.query_similar_agents_with_scores(
            "test", count=5, active_only=False
        )

        # top_k should equal count when active_only=False
        call_args = db_svc.pinecone.query.call_args
        assert call_args[1]["top_k"] == 5

    @pytest.mark.asyncio
    async def test_respects_count_limit(self, db_svc):
        """Test that results are limited to count parameter."""
        from models.agent import AgentStatus

        db_svc.ai_service = MagicMock()
        db_svc.ai_service.get_embedding = AsyncMock(return_value=[0.1])

        # Pinecone returns 5 matches
        mock_matches = [
            {"id": f"agent-{i}", "score": 0.9 - i * 0.1} for i in range(5)
        ]
        db_svc.pinecone = MagicMock()
        db_svc.pinecone.query = MagicMock(return_value=MagicMock(matches=mock_matches))

        agents = []
        for i in range(5):
            agent = MagicMock()
            agent.agent_id = f"agent-{i}"
            agent.agent_status = AgentStatus.active
            agents.append(agent)

        db_svc.mongo.get_agents_with_conditions = AsyncMock(return_value=agents)

        # Request only 2
        result = await db_svc.query_similar_agents_with_scores("test", count=2)

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_handles_dict_and_object_matches(self, db_svc):
        """Test handling of both dict-like and object-like Pinecone matches."""
        from models.agent import AgentStatus

        db_svc.ai_service = MagicMock()
        db_svc.ai_service.get_embedding = AsyncMock(return_value=[0.1])

        # Mix of dict and object-like matches
        mock_dict_match = {"id": "agent-1", "score": 0.9}
        mock_obj_match = MagicMock()
        mock_obj_match.id = "agent-2"
        mock_obj_match.score = 0.8

        db_svc.pinecone = MagicMock()
        db_svc.pinecone.query = MagicMock(
            return_value=MagicMock(matches=[mock_dict_match, mock_obj_match])
        )

        agent1 = MagicMock()
        agent1.agent_id = "agent-1"
        agent1.agent_status = AgentStatus.active
        agent2 = MagicMock()
        agent2.agent_id = "agent-2"
        agent2.agent_status = AgentStatus.active

        db_svc.mongo.get_agents_with_conditions = AsyncMock(
            return_value=[agent1, agent2]
        )

        result = await db_svc.query_similar_agents_with_scores("test", count=2)

        assert len(result) == 2
        assert result[0] == (agent1, 0.9)
        assert result[1] == (agent2, 0.8)
