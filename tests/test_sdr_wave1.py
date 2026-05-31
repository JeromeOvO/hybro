"""
Tests for SDR Wave 1 fixes: idempotency guard (2.5), semaphore (2.13), CORS (2.12).
"""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.request import OrchestrationRequest

# =============================================================================
# Fix 1 (SDR 2.5): Idempotency claim tests
# =============================================================================


class TestClaimUserMessageForProcessing:
    """Tests for the atomic claim method in mongodb.py."""

    @pytest.mark.asyncio
    async def test_claim_succeeds_for_never_claimed(self):
        """claim_user_message_for_processing returns True for unclaimed messages."""
        from database.mongodb import MongoDB

        db = object.__new__(MongoDB)
        mock_collection = MagicMock()
        mock_collection.find_one_and_update = AsyncMock(return_value={"message_id": "m1"})
        db._room_user_messages_collection = mock_collection
        type(db).room_user_messages_collection = property(lambda self: self._room_user_messages_collection)

        result = await db.claim_user_message_for_processing("m1")
        assert result is True

        call_args = mock_collection.find_one_and_update.call_args
        assert call_args[0][0] == {"message_id": "m1", "processing_claimed_at": None}

    @pytest.mark.asyncio
    async def test_claim_fails_for_already_claimed(self):
        """claim_user_message_for_processing returns False if already claimed."""
        from database.mongodb import MongoDB

        db = object.__new__(MongoDB)
        mock_collection = MagicMock()
        mock_collection.find_one_and_update = AsyncMock(return_value=None)
        db._room_user_messages_collection = mock_collection
        type(db).room_user_messages_collection = property(lambda self: self._room_user_messages_collection)

        result = await db.claim_user_message_for_processing("m1")
        assert result is False


class TestClaimOrReclaimUserMessage:
    """Tests for recovery claim method in mongodb.py."""

    @pytest.mark.asyncio
    async def test_reclaim_succeeds_for_stale(self):
        """claim_or_reclaim_user_message returns True for stale-claimed messages."""
        from database.mongodb import MongoDB

        db = object.__new__(MongoDB)
        mock_collection = MagicMock()
        mock_collection.find_one_and_update = AsyncMock(return_value={"message_id": "m1"})
        db._room_user_messages_collection = mock_collection
        type(db).room_user_messages_collection = property(lambda self: self._room_user_messages_collection)

        threshold = datetime(2026, 1, 1, tzinfo=UTC)
        result = await db.claim_or_reclaim_user_message("m1", threshold)
        assert result is True

        call_args = mock_collection.find_one_and_update.call_args
        query = call_args[0][0]
        assert "$or" in query
        assert {"processing_claimed_at": None} in query["$or"]
        assert {"processing_claimed_at": {"$lt": threshold}} in query["$or"]

    @pytest.mark.asyncio
    async def test_reclaim_fails_for_recently_claimed(self):
        """claim_or_reclaim_user_message returns False if recently claimed."""
        from database.mongodb import MongoDB

        db = object.__new__(MongoDB)
        mock_collection = MagicMock()
        mock_collection.find_one_and_update = AsyncMock(return_value=None)
        db._room_user_messages_collection = mock_collection
        type(db).room_user_messages_collection = property(lambda self: self._room_user_messages_collection)

        threshold = datetime(2026, 1, 1, tzinfo=UTC)
        result = await db.claim_or_reclaim_user_message("m1", threshold)
        assert result is False


class TestIdempotencyGuardInRoomMessageCenter:
    """Tests for the idempotency guard in process_room_user_message."""

    @pytest.mark.asyncio
    async def test_normal_claim_rejected_returns_409(self):
        """Second call with same message_id should return 409."""
        from modules.RoomMessageCenter import RoomMessageCenter

        rmc = object.__new__(RoomMessageCenter)
        rmc.database_service = MagicMock()
        rmc.database_service.claim_user_message_for_processing = AsyncMock(return_value=False)
        rmc.sse_manager = MagicMock()

        request = OrchestrationRequest(
            room_id="room-1",
            room_user_message_id="msg-1",
            room_related_message_id="",
        )

        result = await rmc.process_room_user_message(request)
        assert result.success is False
        assert result.status_code == 409
        assert "already being processed" in result.error.lower()

    @pytest.mark.asyncio
    async def test_recovery_uses_reclaim(self):
        """Recovery path with is_recovery=True should use claim_or_reclaim."""
        from modules.RoomMessageCenter import RoomMessageCenter

        rmc = object.__new__(RoomMessageCenter)
        rmc.database_service = MagicMock()
        rmc.database_service.claim_or_reclaim_user_message = AsyncMock(return_value=False)
        rmc.sse_manager = MagicMock()

        request = OrchestrationRequest(
            room_id="room-1",
            room_user_message_id="msg-1",
            room_related_message_id="",
            is_recovery=True,
        )

        result = await rmc.process_room_user_message(request)
        assert result.success is False
        assert result.status_code == 409
        rmc.database_service.claim_or_reclaim_user_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_recovery_threshold_uses_orphan_threshold(self):
        """Recovery reclaim threshold must use orphan_threshold_minutes, not processing_status_expiry_minutes."""
        from common.utils.time import utcnow as real_utcnow
        from modules.RoomMessageCenter import RoomMessageCenter

        rmc = object.__new__(RoomMessageCenter)
        rmc.database_service = MagicMock()
        rmc.database_service.claim_or_reclaim_user_message = AsyncMock(return_value=False)
        rmc.sse_manager = MagicMock()

        request = OrchestrationRequest(
            room_id="room-1",
            room_user_message_id="msg-1",
            room_related_message_id="",
            is_recovery=True,
        )

        with patch("modules.RoomMessageCenter.settings") as mock_settings:
            mock_settings.orphan_threshold_minutes = 2
            mock_settings.processing_status_expiry_minutes = 30
            await rmc.process_room_user_message(request)

        call_args = rmc.database_service.claim_or_reclaim_user_message.call_args
        threshold_arg = call_args[0][1]
        now = real_utcnow()
        # The threshold should be ~2 minutes ago (orphan), not ~30 minutes ago
        delta = now - threshold_arg
        assert delta.total_seconds() < 300, (
            f"Threshold is {delta.total_seconds():.0f}s ago, expected ~120s (orphan_threshold_minutes=2)"
        )


# =============================================================================
# Fix 3 (SDR 2.13): Semaphore existence test
# =============================================================================


class TestStaleTaskCheckerSemaphore:
    """Tests for bounded recovery scheduling."""

    def test_recovery_semaphore_exists_with_correct_value(self):
        from jobs.stale_task_checker import MAX_CONCURRENT_RECOVERIES, StaleTaskChecker

        checker = StaleTaskChecker()
        assert hasattr(checker, "_recovery_semaphore")
        assert isinstance(checker._recovery_semaphore, asyncio.Semaphore)
        assert checker._recovery_semaphore._value == MAX_CONCURRENT_RECOVERIES
        assert MAX_CONCURRENT_RECOVERIES == 5

    @pytest.mark.asyncio
    async def test_orphan_recovery_uses_execution_scheduler(self, monkeypatch):
        from jobs import stale_task_checker as mod
        from jobs.stale_task_checker import StaleRecoveryDeps, StaleTaskChecker

        checker = StaleTaskChecker()
        scheduled = []

        def schedule_recovery(request, *, reason):
            scheduled.append((request, reason))
            return MagicMock(add_done_callback=MagicMock())

        checker.set_execution_recovery_deps(
            StaleRecoveryDeps(schedule_recovery=schedule_recovery)
        )
        monkeypatch.setattr(
            mod.db_service,
            "get_orphaned_agent_messages",
            AsyncMock(
                return_value=[
                    MagicMock(
                        agent_id="agent-1",
                        related_message_id="user-msg-1",
                        room_id="room-1",
                        message_id="agent-msg-1",
                    )
                ]
            ),
        )
        monkeypatch.setattr(
            mod.db_service,
            "get_agent_by_agent_id",
            AsyncMock(return_value=None),
        )

        await checker._recover_orphaned_messages()

        assert len(scheduled) == 1
        request, reason = scheduled[0]
        assert reason == "orphan"
        assert request.room_user_message_id == "user-msg-1"

    @pytest.mark.asyncio
    async def test_orphan_recovery_skips_before_execution_scheduler_bind(
        self,
        monkeypatch,
    ):
        from jobs import stale_task_checker as mod
        from jobs.stale_task_checker import StaleTaskChecker

        checker = StaleTaskChecker()
        monkeypatch.setattr(
            mod.db_service,
            "get_orphaned_agent_messages",
            AsyncMock(return_value=[MagicMock()]),
        )
        get_agent = AsyncMock()
        monkeypatch.setattr(mod.db_service, "get_agent_by_agent_id", get_agent)

        await checker._recover_orphaned_messages()

        get_agent.assert_not_called()

    @pytest.mark.asyncio
    async def test_supervisor_recovery_uses_execution_scheduler(self, monkeypatch):
        from jobs import stale_task_checker as mod
        from jobs.stale_task_checker import StaleRecoveryDeps, StaleTaskChecker

        checker = StaleTaskChecker()
        scheduled = []

        def schedule_recovery(request, *, reason):
            scheduled.append((request, reason))
            return MagicMock(add_done_callback=MagicMock())

        checker.set_execution_recovery_deps(
            StaleRecoveryDeps(schedule_recovery=schedule_recovery)
        )
        monkeypatch.setattr(
            mod.db_service,
            "get_stuck_supervisor_trajectory_messages",
            AsyncMock(return_value=[{"message_id": "msg-1", "room_id": "room-1"}]),
        )
        monkeypatch.setattr(
            mod.db_service,
            "is_message_cancelled",
            AsyncMock(return_value=False),
        )
        monkeypatch.setattr(
            mod.db_service,
            "claim_stuck_supervisor_trajectory",
            AsyncMock(return_value=True),
        )

        await checker._recover_stuck_supervisor_trajectories()

        assert len(scheduled) == 1
        request, reason = scheduled[0]
        assert reason == "supervisor"
        assert request.room_user_message_id == "msg-1"

    @pytest.mark.asyncio
    async def test_stale_checker_uses_bound_hitl_recovery_deps(self, monkeypatch):
        from jobs import stale_task_checker as mod
        from jobs.stale_task_checker import StaleHITLDeps, StaleTaskChecker

        checker = StaleTaskChecker()
        recover = AsyncMock()
        checker.set_hitl_deps(
            StaleHITLDeps(
                recover_stale_processing=recover,
                cancel_requests_for_message=AsyncMock(),
            )
        )
        monkeypatch.setattr(
            mod.db_service,
            "get_stale_task_messages",
            AsyncMock(return_value=[]),
        )
        monkeypatch.setattr(
            mod.db_service,
            "get_expired_task_messages",
            AsyncMock(return_value=[]),
        )
        monkeypatch.setattr(
            mod.db_service,
            "get_orphaned_agent_messages",
            AsyncMock(return_value=[]),
        )
        monkeypatch.setattr(
            mod.db_service,
            "get_room_ids_with_non_terminal_runs",
            AsyncMock(return_value=[]),
        )
        monkeypatch.setattr(
            mod.db_service,
            "get_non_tracked_stale_task_messages",
            AsyncMock(return_value=[]),
        )
        monkeypatch.setattr(
            mod.db_service,
            "get_stuck_supervisor_trajectory_messages",
            AsyncMock(return_value=[]),
        )
        monkeypatch.setattr(
            mod.db_service,
            "find_stale_non_terminal_runs",
            AsyncMock(return_value=[]),
        )

        await checker.check_stale_tasks()

        recover.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mark_task_failed_cancels_hitl_through_bound_deps(self, monkeypatch):
        from jobs import stale_task_checker as mod
        from jobs.stale_task_checker import StaleHITLDeps, StaleTaskChecker

        checker = StaleTaskChecker()
        cancel = AsyncMock()
        checker.set_hitl_deps(
            StaleHITLDeps(
                recover_stale_processing=AsyncMock(),
                cancel_requests_for_message=cancel,
            )
        )
        msg = MagicMock()
        msg.message_content.message_task.id = "task-1"
        msg.message_content.message_task.context_id = "ctx-1"
        msg.related_message_id = "user-msg-1"
        msg.room_id = "room-1"
        msg.user_id = "user-1"
        monkeypatch.setattr(
            mod.db_service,
            "update_task_on_message",
            AsyncMock(),
        )
        monkeypatch.setattr(
            mod.db_service,
            "get_and_clear_continuation_on_message",
            AsyncMock(),
        )
        monkeypatch.setattr(
            mod.db_service,
            "get_and_clear_continuation_on_user_message",
            AsyncMock(),
        )
        monkeypatch.setattr(mod, "notify_task_update", AsyncMock())

        await checker._mark_task_failed(
            message_id="agent-msg-1",
            msg=msg,
            error="failed",
        )

        cancel.assert_awaited_once_with("user-msg-1")


# =============================================================================
# Fix 4 (SDR 2.12): CORS configuration test
# =============================================================================


class TestCORSConfiguration:
    """Tests for tightened CORS headers."""

    def test_cors_returns_explicit_methods_and_headers(self):
        """OPTIONS preflight should return specific allow_methods and allow_headers."""
        from fastapi.testclient import TestClient

        from main import app

        # Patch auth dependency to avoid Clerk calls
        with patch("common.auth.get_current_user", return_value=MagicMock()):
            client = TestClient(app)
            response = client.options(
                "/api/v1/roomCenter/sendMessage",
                headers={
                    "Origin": "http://localhost:3000",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "Authorization,Content-Type",
                },
            )

        # CORS middleware should respond to preflight
        allow_methods = response.headers.get("access-control-allow-methods", "")
        allow_headers = response.headers.get("access-control-allow-headers", "")

        expected_methods = {"GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"}
        # Our configured headers — the framework may also add CORS-safelisted headers
        configured_headers = {"Authorization", "Content-Type", "X-API-Key", "Cache-Control", "sentry-trace", "baggage"}

        actual_methods = {m.strip() for m in allow_methods.split(",")} if allow_methods else set()
        actual_headers = {h.strip() for h in allow_headers.split(",")} if allow_headers else set()

        assert expected_methods == actual_methods
        assert configured_headers.issubset(actual_headers)
        # Wildcard headers should NOT be present
        assert "*" not in allow_headers
