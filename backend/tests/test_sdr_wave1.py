"""
Tests for SDR Wave 1 fixes: idempotency guard (2.5), semaphore (2.13), CORS (2.12).
"""

import ast
import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from common.utils.time import utcnow
from models.request import OrchestrationRequest

# =============================================================================
# Fix 1 (SDR 2.5): Idempotency claim tests
# =============================================================================


class TestClaimUserMessageForProcessing:
    """Tests for the atomic claim method on the repository store."""

    @pytest.mark.asyncio
    async def test_claim_succeeds_for_never_claimed(self):
        """claim_user_message_for_processing returns True for unclaimed messages."""
        from dal.runtime_store import RuntimeRepositoryStore

        store = object.__new__(RuntimeRepositoryStore)
        mock_collection = MagicMock()
        mock_collection.find_one_and_update = AsyncMock(
            return_value={"message_id": "m1"}
        )
        store._room_user_messages = mock_collection

        result = await store.claim_user_message_for_processing("m1")
        assert result is True

        call_args = mock_collection.find_one_and_update.call_args
        assert call_args[0][0] == {"message_id": "m1", "processing_claimed_at": None}

    @pytest.mark.asyncio
    async def test_claim_fails_for_already_claimed(self):
        """claim_user_message_for_processing returns False if already claimed."""
        from dal.runtime_store import RuntimeRepositoryStore

        store = object.__new__(RuntimeRepositoryStore)
        mock_collection = MagicMock()
        mock_collection.find_one_and_update = AsyncMock(return_value=None)
        store._room_user_messages = mock_collection

        result = await store.claim_user_message_for_processing("m1")
        assert result is False


class TestClaimOrReclaimUserMessage:
    """Tests for the recovery claim method on the repository store."""

    @pytest.mark.asyncio
    async def test_reclaim_succeeds_for_stale(self):
        """claim_or_reclaim_user_message returns True for stale-claimed messages."""
        from dal.runtime_store import RuntimeRepositoryStore

        store = object.__new__(RuntimeRepositoryStore)
        mock_collection = MagicMock()
        mock_collection.find_one_and_update = AsyncMock(
            return_value={"message_id": "m1"}
        )
        store._room_user_messages = mock_collection

        threshold = datetime(2026, 1, 1, tzinfo=UTC)
        result = await store.claim_or_reclaim_user_message("m1", threshold)
        assert result is True

        call_args = mock_collection.find_one_and_update.call_args
        query = call_args[0][0]
        assert "$or" in query
        assert {"processing_claimed_at": None} in query["$or"]
        assert {"processing_claimed_at": {"$lt": threshold}} in query["$or"]
        assert {
            "processing_claimed_at": {
                "$type": "string",
                "$lt": "2026-01-01T00:00:00.000000",
            }
        } in query["$or"]

    @pytest.mark.asyncio
    async def test_reclaim_fails_for_recently_claimed(self):
        """claim_or_reclaim_user_message returns False if recently claimed."""
        from dal.runtime_store import RuntimeRepositoryStore

        store = object.__new__(RuntimeRepositoryStore)
        mock_collection = MagicMock()
        mock_collection.find_one_and_update = AsyncMock(return_value=None)
        store._room_user_messages = mock_collection

        threshold = datetime(2026, 1, 1, tzinfo=UTC)
        result = await store.claim_or_reclaim_user_message("m1", threshold)
        assert result is False


class TestIdempotencyGuardInRoomMessageCenter:
    """Tests for the idempotency guard in process_room_user_message."""

    @pytest.mark.asyncio
    async def test_hitl_resume_reuses_existing_processing_claim(self):
        """An immediate HITL resume must not wait for its own claim to become stale."""
        from execution.orchestration.room_message_center import RoomMessageCenter

        rmc = object.__new__(RoomMessageCenter)
        rmc.message_writer = MagicMock()
        rmc.message_writer.refresh_processing_claim = AsyncMock(return_value=True)
        rmc.message_writer.claim_or_reclaim_user_message = AsyncMock(return_value=False)

        request = MagicMock(
            room_user_message_id="msg-1",
            is_recovery=True,
            reuse_processing_claim=True,
        )

        claimed = await rmc._claim_user_message(request)

        assert claimed is True
        rmc.message_writer.refresh_processing_claim.assert_awaited_once_with("msg-1")
        rmc.message_writer.claim_or_reclaim_user_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_normal_claim_rejected_returns_409(self):
        """Second call with same message_id should return 409."""
        from execution.orchestration.room_message_center import RoomMessageCenter

        rmc = object.__new__(RoomMessageCenter)
        rmc.message_writer = MagicMock()
        rmc.message_writer.claim_user_message_for_processing = AsyncMock(
            return_value=False
        )
        rmc.delivery = MagicMock()

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
        from execution.orchestration.room_message_center import RoomMessageCenter

        rmc = object.__new__(RoomMessageCenter)
        rmc.message_writer = MagicMock()
        rmc.message_writer.claim_or_reclaim_user_message = AsyncMock(return_value=False)
        rmc.delivery = MagicMock()

        request = OrchestrationRequest(
            room_id="room-1",
            room_user_message_id="msg-1",
            room_related_message_id="",
            is_recovery=True,
        )

        result = await rmc.process_room_user_message(request)
        assert result.success is False
        assert result.status_code == 409
        rmc.message_writer.claim_or_reclaim_user_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_recovery_threshold_uses_orphan_threshold(self):
        """Recovery reclaim threshold must use orphan_threshold_minutes, not processing_status_expiry_minutes."""
        from execution.orchestration.room_message_center import RoomMessageCenter

        rmc = object.__new__(RoomMessageCenter)
        rmc.message_writer = MagicMock()
        rmc.message_writer.claim_or_reclaim_user_message = AsyncMock(return_value=False)
        rmc.delivery = MagicMock()
        rmc.orphan_threshold_minutes = 2

        request = OrchestrationRequest(
            room_id="room-1",
            room_user_message_id="msg-1",
            room_related_message_id="",
            is_recovery=True,
        )

        before = utcnow()
        await rmc.process_room_user_message(request)
        after = utcnow()

        call_args = rmc.message_writer.claim_or_reclaim_user_message.call_args
        threshold_arg = call_args[0][1]
        assert (
            before - timedelta(minutes=2)
            <= threshold_arg
            <= after - timedelta(minutes=2)
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
            mod.store,
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
            mod.store,
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
            mod.store,
            "get_orphaned_agent_messages",
            AsyncMock(return_value=[MagicMock()]),
        )
        get_agent = AsyncMock()
        monkeypatch.setattr(mod.store, "get_agent_by_agent_id", get_agent)

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
            mod.store,
            "get_stuck_supervisor_trajectory_messages",
            AsyncMock(return_value=[{"message_id": "msg-1", "room_id": "room-1"}]),
        )
        monkeypatch.setattr(
            mod.store,
            "is_message_cancelled",
            AsyncMock(return_value=False),
        )
        monkeypatch.setattr(
            mod.store,
            "claim_stuck_supervisor_trajectory",
            AsyncMock(return_value=True),
        )

        await checker._recover_stuck_supervisor_trajectories()

        assert len(scheduled) == 1
        request, reason = scheduled[0]
        assert reason == "supervisor"
        assert request.room_user_message_id == "msg-1"

    @pytest.mark.asyncio
    async def test_v2_orchestration_recovery_uses_sidecar_run_store(self):
        from jobs.stale_task_checker import (
            StaleOrchestrationRunRecoveryDeps,
            StaleRecoveryDeps,
            StaleTaskChecker,
            StaleTaskCheckerDeps,
        )
        from models.orchestration import (
            OrchestrationEventType,
            OrchestrationRunState,
            OrchestrationStatus,
        )

        checker = StaleTaskChecker(orphan_threshold_minutes=2)
        scheduled = []

        def schedule_recovery(request, *, reason):
            scheduled.append((request, reason))
            return MagicMock(add_done_callback=MagicMock())

        run_state = OrchestrationRunState(
            run_id="run-1",
            room_id="room-1",
            user_message_id="msg-1",
            goal="Coordinate this",
            candidate_agent_ids=["agent-1"],
            status=OrchestrationStatus.DISPATCHING,
            updated_at=utcnow() - timedelta(minutes=10),
        )
        run_store = SimpleNamespace(
            list_recoverable=AsyncMock(return_value=[run_state]),
            save_state=AsyncMock(side_effect=lambda state, **_kwargs: state),
            append_event=AsyncMock(),
        )
        checker.set_runtime_deps(
            StaleTaskCheckerDeps(
                store=SimpleNamespace(
                    is_message_cancelled=AsyncMock(return_value=False),
                    get_room_user_message_by_message_id=AsyncMock(return_value=None),
                ),
                rooms_collection=None,
                notify_task_update=AsyncMock(),
                increment_counter=MagicMock(),
                a2a_service=SimpleNamespace(),
            )
        )
        checker.set_execution_recovery_deps(
            StaleRecoveryDeps(schedule_recovery=schedule_recovery)
        )
        checker.set_orchestration_run_recovery_deps(
            StaleOrchestrationRunRecoveryDeps(orchestration_run_store=run_store)
        )

        await checker._recover_stuck_orchestration_runs()

        run_store.list_recoverable.assert_awaited_once()
        run_store.save_state.assert_awaited_once()
        run_store.append_event.assert_awaited_once()
        event = run_store.append_event.await_args.args[0]
        assert event.type == OrchestrationEventType.RUN_RECOVERED
        assert len(scheduled) == 1
        request, reason = scheduled[0]
        assert reason == "orchestration_v2"
        assert request.room_id == "room-1"
        assert request.room_user_message_id == "msg-1"

    @pytest.mark.asyncio
    async def test_v2_orchestration_recovery_skips_fresh_processing_claim(self):
        from jobs.stale_task_checker import (
            StaleOrchestrationRunRecoveryDeps,
            StaleRecoveryDeps,
            StaleTaskChecker,
            StaleTaskCheckerDeps,
        )
        from models.orchestration import OrchestrationRunState, OrchestrationStatus

        checker = StaleTaskChecker(orphan_threshold_minutes=2)
        scheduled = []

        def schedule_recovery(request, *, reason):
            scheduled.append((request, reason))
            return MagicMock(add_done_callback=MagicMock())

        run_state = OrchestrationRunState(
            run_id="run-1",
            room_id="room-1",
            user_message_id="msg-1",
            goal="Coordinate this",
            candidate_agent_ids=["agent-1"],
            status=OrchestrationStatus.DISPATCHING,
            updated_at=utcnow() - timedelta(minutes=10),
        )
        run_store = SimpleNamespace(
            list_recoverable=AsyncMock(return_value=[run_state]),
            save_state=AsyncMock(),
            append_event=AsyncMock(),
        )
        checker.set_runtime_deps(
            StaleTaskCheckerDeps(
                store=SimpleNamespace(
                    is_message_cancelled=AsyncMock(return_value=False),
                    get_room_user_message_by_message_id=AsyncMock(
                        return_value=SimpleNamespace(processing_claimed_at=utcnow())
                    ),
                ),
                rooms_collection=None,
                notify_task_update=AsyncMock(),
                increment_counter=MagicMock(),
                a2a_service=SimpleNamespace(),
            )
        )
        checker.set_execution_recovery_deps(
            StaleRecoveryDeps(schedule_recovery=schedule_recovery)
        )
        checker.set_orchestration_run_recovery_deps(
            StaleOrchestrationRunRecoveryDeps(orchestration_run_store=run_store)
        )

        await checker._recover_stuck_orchestration_runs()

        assert scheduled == []
        run_store.save_state.assert_not_awaited()
        run_store.append_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_v2_orchestration_recovery_fails_closed_without_claim_reader(
        self, caplog
    ):
        from jobs.stale_task_checker import (
            StaleOrchestrationRunRecoveryDeps,
            StaleRecoveryDeps,
            StaleTaskChecker,
            StaleTaskCheckerDeps,
        )
        from models.orchestration import OrchestrationRunState, OrchestrationStatus

        checker = StaleTaskChecker(orphan_threshold_minutes=2)
        scheduled = []
        run_state = OrchestrationRunState(
            run_id="run-1",
            room_id="room-1",
            user_message_id="msg-1",
            goal="Coordinate this",
            candidate_agent_ids=["agent-1"],
            status=OrchestrationStatus.DISPATCHING,
            updated_at=utcnow() - timedelta(minutes=10),
        )
        run_store = SimpleNamespace(
            list_recoverable=AsyncMock(return_value=[run_state]),
            save_state=AsyncMock(),
            append_event=AsyncMock(),
        )
        checker.set_runtime_deps(
            StaleTaskCheckerDeps(
                store=SimpleNamespace(
                    is_message_cancelled=AsyncMock(return_value=False)
                ),
                rooms_collection=None,
                notify_task_update=AsyncMock(),
                increment_counter=MagicMock(),
                a2a_service=SimpleNamespace(),
            )
        )
        checker.set_execution_recovery_deps(
            StaleRecoveryDeps(
                schedule_recovery=lambda request, *, reason: scheduled.append(
                    (request, reason)
                )
            )
        )
        checker.set_orchestration_run_recovery_deps(
            StaleOrchestrationRunRecoveryDeps(orchestration_run_store=run_store)
        )

        await checker._recover_stuck_orchestration_runs()

        assert scheduled == []
        run_store.save_state.assert_not_awaited()
        run_store.append_event.assert_not_awaited()
        assert "processing-claim reader is not bound" in caplog.text

    @pytest.mark.asyncio
    async def test_v2_orchestration_recovery_skips_awaiting_user_runs(self):
        from jobs.stale_task_checker import (
            StaleOrchestrationRunRecoveryDeps,
            StaleRecoveryDeps,
            StaleTaskChecker,
            StaleTaskCheckerDeps,
        )
        from models.orchestration import OrchestrationRunState, OrchestrationStatus

        checker = StaleTaskChecker(orphan_threshold_minutes=2)
        scheduled = []

        def schedule_recovery(request, *, reason):
            scheduled.append((request, reason))
            return MagicMock(add_done_callback=MagicMock())

        run_state = OrchestrationRunState(
            run_id="run-1",
            room_id="room-1",
            user_message_id="msg-1",
            goal="Coordinate this",
            candidate_agent_ids=["agent-1"],
            status=OrchestrationStatus.AWAITING_USER,
            pending_hitl_request_ids=["hitl-1"],
            updated_at=utcnow() - timedelta(minutes=10),
        )
        run_store = SimpleNamespace(
            list_recoverable=AsyncMock(return_value=[run_state]),
            save_state=AsyncMock(),
            append_event=AsyncMock(),
        )
        checker.set_runtime_deps(
            StaleTaskCheckerDeps(
                store=SimpleNamespace(
                    is_message_cancelled=AsyncMock(return_value=False),
                    get_room_user_message_by_message_id=AsyncMock(return_value=None),
                ),
                rooms_collection=None,
                notify_task_update=AsyncMock(),
                increment_counter=MagicMock(),
                a2a_service=SimpleNamespace(),
            )
        )
        checker.set_execution_recovery_deps(
            StaleRecoveryDeps(schedule_recovery=schedule_recovery)
        )
        checker.set_orchestration_run_recovery_deps(
            StaleOrchestrationRunRecoveryDeps(orchestration_run_store=run_store)
        )

        await checker._recover_stuck_orchestration_runs()

        assert scheduled == []
        run_store.save_state.assert_not_awaited()
        run_store.append_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_v2_orchestration_recovery_recovers_hitl_artifact_creating_runs(self):
        from jobs.stale_task_checker import (
            StaleOrchestrationRunRecoveryDeps,
            StaleRecoveryDeps,
            StaleTaskChecker,
            StaleTaskCheckerDeps,
        )
        from models.orchestration import OrchestrationRunState, OrchestrationStatus

        checker = StaleTaskChecker(orphan_threshold_minutes=2)
        scheduled = []

        def schedule_recovery(request, *, reason):
            scheduled.append((request, reason))
            return MagicMock(add_done_callback=MagicMock())

        run_state = OrchestrationRunState(
            run_id="run-1",
            room_id="room-1",
            user_message_id="msg-1",
            goal="Coordinate this",
            candidate_agent_ids=["agent-1"],
            status=OrchestrationStatus.INGESTING,
            pending_hitl_request_ids=["run-1:step-1:supervisor-hitl-1"],
            updated_at=utcnow() - timedelta(minutes=10),
        )
        run_store = SimpleNamespace(
            list_recoverable=AsyncMock(return_value=[run_state]),
            save_state=AsyncMock(side_effect=lambda state, **_kwargs: state),
            append_event=AsyncMock(),
        )
        checker.set_runtime_deps(
            StaleTaskCheckerDeps(
                store=SimpleNamespace(
                    is_message_cancelled=AsyncMock(return_value=False),
                    get_room_user_message_by_message_id=AsyncMock(return_value=None),
                ),
                rooms_collection=None,
                notify_task_update=AsyncMock(),
                increment_counter=MagicMock(),
                a2a_service=SimpleNamespace(),
            )
        )
        checker.set_execution_recovery_deps(
            StaleRecoveryDeps(schedule_recovery=schedule_recovery)
        )
        checker.set_orchestration_run_recovery_deps(
            StaleOrchestrationRunRecoveryDeps(orchestration_run_store=run_store)
        )

        await checker._recover_stuck_orchestration_runs()

        run_store.save_state.assert_awaited_once()
        assert len(scheduled) == 1
        request, reason = scheduled[0]
        assert reason == "orchestration_v2"
        assert request.room_user_message_id == "msg-1"

    def test_container_binds_processing_claim_reader_to_stale_task_store(self):
        tree = ast.parse(Path("container.py").read_text())
        stale_task_store = next(
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "stale_task_store"
                for target in node.targets
            )
        )
        bindings = {
            keyword.arg: ast.unparse(keyword.value)
            for keyword in stale_task_store.keywords
        }

        assert bindings["get_room_user_message_by_message_id"] == (
            "message_store.get_room_user_message_by_message_id"
        )

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
            mod.store,
            "get_stale_task_messages",
            AsyncMock(return_value=[]),
        )
        monkeypatch.setattr(
            mod.store,
            "get_expired_task_messages",
            AsyncMock(return_value=[]),
        )
        monkeypatch.setattr(
            mod.store,
            "get_orphaned_agent_messages",
            AsyncMock(return_value=[]),
        )
        monkeypatch.setattr(
            mod.store,
            "get_room_ids_with_non_terminal_runs",
            AsyncMock(return_value=[]),
        )
        monkeypatch.setattr(
            mod.store,
            "get_non_tracked_stale_task_messages",
            AsyncMock(return_value=[]),
        )
        monkeypatch.setattr(
            mod.store,
            "get_stuck_supervisor_trajectory_messages",
            AsyncMock(return_value=[]),
        )
        monkeypatch.setattr(
            mod.store,
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
            mod.store,
            "update_task_on_message",
            AsyncMock(),
        )
        monkeypatch.setattr(
            mod.store,
            "get_and_clear_continuation_on_message",
            AsyncMock(),
        )
        monkeypatch.setattr(
            mod.store,
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
        configured_headers = {
            "Authorization",
            "Content-Type",
            "X-API-Key",
            "Cache-Control",
            "sentry-trace",
            "baggage",
        }

        actual_methods = (
            {m.strip() for m in allow_methods.split(",")} if allow_methods else set()
        )
        actual_headers = (
            {h.strip() for h in allow_headers.split(",")} if allow_headers else set()
        )

        assert expected_methods == actual_methods
        assert configured_headers.issubset(actual_headers)
        # Wildcard headers should NOT be present
        assert "*" not in allow_headers
