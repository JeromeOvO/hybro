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
        monkeypatch.setattr(
            mod.store,
            "is_message_cancelled",
            AsyncMock(return_value=False),
        )
        monkeypatch.setattr(
            mod.store,
            "is_message_cancelled_strict",
            AsyncMock(return_value=False),
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
    async def test_orchestration_envelope_query_filters_before_bounded_scan(self):
        from dal.runtime_store.parts.message_store import MessageRuntimeStorePart

        user_messages = SimpleNamespace(find=AsyncMock(return_value=[]))
        part = MessageRuntimeStorePart(
            room_agent_messages=SimpleNamespace(),
            room_user_messages=user_messages,
            message_repository=SimpleNamespace(),
        )

        assert await part.get_stale_claimed_orchestration_messages(2, limit=25) == []

        query = user_messages.find.await_args.args[0]
        assert query["extend_info.orchestration"] is True
        assert query["extend_info.orchestration_status"] == "created"
        unclaimed_created = query["$or"][0]
        assert unclaimed_created["processing_claimed_at"] is None
        created_at_predicates = unclaimed_created["$or"]
        assert created_at_predicates[0]["message_created_at"].get("$lt") is not None
        assert created_at_predicates[1]["message_created_at"]["$type"] == "string"
        assert created_at_predicates[1]["message_created_at"].get("$lt")
        assert query["$or"][1]["processing_claimed_at"].get("$lt") is not None
        assert query["message_id"] == {"$type": "string"}
        assert user_messages.find.await_args.kwargs["limit"] == 25

    @pytest.mark.asyncio
    async def test_projection_repair_is_compare_and_set(self):
        from dal.runtime_store.parts.message_store import MessageRuntimeStorePart

        user_messages = SimpleNamespace(update_one=AsyncMock(return_value=True))
        part = MessageRuntimeStorePart(
            room_agent_messages=SimpleNamespace(),
            room_user_messages=user_messages,
            message_repository=SimpleNamespace(),
        )

        assert await part.update_orchestration_projection_if_status(
            "message-1",
            expected_status="created",
            status="completed",
            clear_processing_claim=True,
        )

        user_messages.update_one.assert_awaited_once_with(
            {
                "message_id": "message-1",
                "extend_info.orchestration_status": "created",
            },
            {
                "$set": {
                    "extend_info.orchestration_status": "completed",
                    "processing_claimed_at": None,
                }
            },
        )

    @pytest.mark.asyncio
    async def test_terminal_envelopes_do_not_starve_bounded_recovery_query(self):
        from dal.runtime_store.parts.message_store import MessageRuntimeStorePart
        from models.room import MessageContent, RoomUserMessage

        terminal_docs = [
            RoomUserMessage(
                room_id="room-1",
                message_id=f"terminal-{index:03d}",
                user_id="user-1",
                message_content=MessageContent(message_text="done"),
                extend_info={
                    "orchestration": True,
                    "orchestration_status": "completed",
                },
                processing_claimed_at=utcnow() - timedelta(minutes=10),
            ).model_dump(mode="json")
            for index in range(100)
        ]
        stranded_doc = RoomUserMessage(
            room_id="room-1",
            message_id="stranded-101",
            user_id="user-1",
            message_content=MessageContent(message_text="recover me"),
            extend_info={
                "orchestration": True,
                "orchestration_status": "created",
            },
            processing_claimed_at=None,
            message_created_at=utcnow() - timedelta(minutes=10),
        ).model_dump(mode="json")

        class FilteringCollection:
            async def find(self, query, *, sort, limit):
                recoverable_status = query["extend_info.orchestration_status"]
                recoverable = [
                    doc
                    for doc in [*terminal_docs, stranded_doc]
                    if doc["extend_info"]["orchestration_status"] == recoverable_status
                ]
                return recoverable[:limit]

        assert isinstance(stranded_doc["message_created_at"], str)
        part = MessageRuntimeStorePart(
            room_agent_messages=SimpleNamespace(),
            room_user_messages=FilteringCollection(),
            message_repository=SimpleNamespace(),
        )

        messages = await part.get_stale_claimed_orchestration_messages(2, limit=100)

        assert [message.message_id for message in messages] == ["stranded-101"]

    @pytest.mark.asyncio
    async def test_malformed_envelope_cannot_truncate_keyset_page(self):
        from dal.runtime_store.parts.message_store import MessageRuntimeStorePart
        from models.room import MessageContent, RoomUserMessage

        valid_docs = [
            RoomUserMessage(
                room_id="room-1",
                message_id=f"created-{index:03d}",
                user_id="user-1",
                message_content=MessageContent(message_text="existing"),
                extend_info={
                    "orchestration": True,
                    "orchestration_status": "created",
                },
                processing_claimed_at=utcnow() - timedelta(minutes=10),
            ).model_dump(mode="json")
            for index in range(99)
        ]
        malformed_doc = {
            "message_id": "created-099",
            "room_id": "room-1",
            "extend_info": {
                "orchestration": True,
                "orchestration_status": "created",
            },
            "processing_claimed_at": (utcnow() - timedelta(minutes=10)).isoformat(),
        }
        stranded_doc = RoomUserMessage(
            room_id="room-1",
            message_id="stranded-100",
            user_id="user-1",
            message_content=MessageContent(message_text="recover me"),
            extend_info={
                "orchestration": True,
                "orchestration_status": "created",
            },
            processing_claimed_at=utcnow() - timedelta(minutes=10),
        ).model_dump(mode="json")

        class KeysetCollection:
            def __init__(self):
                self.calls = 0

            async def find(self, query, *, sort, limit):
                self.calls += 1
                after = query["message_id"].get("$gt")
                docs = [*valid_docs, malformed_doc, stranded_doc]
                if after is not None:
                    docs = [doc for doc in docs if doc["message_id"] > after]
                return docs[:limit]

        collection = KeysetCollection()
        part = MessageRuntimeStorePart(
            room_agent_messages=SimpleNamespace(),
            room_user_messages=collection,
            message_repository=SimpleNamespace(),
        )

        messages = await part.get_stale_claimed_orchestration_messages(2, limit=100)

        assert len(messages) == 100
        assert messages[-1].message_id == "stranded-100"
        assert collection.calls == 2

    @pytest.mark.asyncio
    async def test_claimed_supervisor_envelope_without_run_is_recovered(self):
        from jobs.stale_task_checker import (
            StaleOrchestrationRunRecoveryDeps,
            StaleRecoveryDeps,
            StaleTaskChecker,
            StaleTaskCheckerDeps,
        )

        checker = StaleTaskChecker(orphan_threshold_minutes=2)
        scheduled = []

        def schedule_recovery(request, *, reason):
            scheduled.append((request, reason))
            return MagicMock(add_done_callback=MagicMock())

        stale_message = SimpleNamespace(
            message_id="msg-before-run",
            room_id="room-1",
            extend_info={"orchestration": {"goal": "Coordinate this"}},
        )
        store = SimpleNamespace(
            get_stale_claimed_orchestration_messages=AsyncMock(
                return_value=[stale_message]
            ),
            is_message_cancelled=AsyncMock(return_value=False),
        )
        run_store = SimpleNamespace(
            get_latest_by_user_message_id=AsyncMock(return_value=None)
        )
        checker.set_runtime_deps(
            StaleTaskCheckerDeps(
                store=store,
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

        await checker._recover_claimed_orchestration_envelopes()

        store.get_stale_claimed_orchestration_messages.assert_awaited_once_with(
            2,
            limit=100,
            after_message_id=None,
        )
        run_store.get_latest_by_user_message_id.assert_awaited_once_with(
            "msg-before-run"
        )
        assert len(scheduled) == 1
        request, reason = scheduled[0]
        assert reason == "orchestration-envelope"
        assert request.room_id == "room-1"
        assert request.room_user_message_id == "msg-before-run"
        assert request.is_recovery is True

    @pytest.mark.asyncio
    async def test_existing_terminal_run_repairs_stale_created_envelope(self):
        from jobs.stale_task_checker import (
            StaleOrchestrationRunRecoveryDeps,
            StaleRecoveryDeps,
            StaleTaskChecker,
            StaleTaskCheckerDeps,
        )
        from models.orchestration import OrchestrationStatus

        message = SimpleNamespace(
            message_id="message-1",
            room_id="room-1",
            extend_info={"orchestration_status": "created"},
            processing_claimed_at=utcnow() - timedelta(minutes=10),
        )
        store = SimpleNamespace(
            get_stale_claimed_orchestration_messages=AsyncMock(
                side_effect=[[message], []]
            ),
            is_message_cancelled=AsyncMock(return_value=False),
            update_orchestration_projection_if_status=AsyncMock(return_value=True),
        )
        run_store = SimpleNamespace(
            get_latest_by_user_message_id=AsyncMock(
                return_value=SimpleNamespace(status=OrchestrationStatus.COMPLETED)
            )
        )
        checker = StaleTaskChecker(orphan_threshold_minutes=2)
        checker.set_runtime_deps(
            StaleTaskCheckerDeps(
                store=store,
                rooms_collection=None,
                notify_task_update=AsyncMock(),
                increment_counter=MagicMock(),
                a2a_service=SimpleNamespace(),
            )
        )
        checker.set_execution_recovery_deps(
            StaleRecoveryDeps(schedule_recovery=MagicMock())
        )
        checker.set_orchestration_run_recovery_deps(
            StaleOrchestrationRunRecoveryDeps(orchestration_run_store=run_store)
        )

        await checker._recover_claimed_orchestration_envelopes()

        assert message.extend_info["orchestration_status"] == "completed"
        assert message.processing_claimed_at is None
        store.update_orchestration_projection_if_status.assert_awaited_once_with(
            "message-1",
            expected_status="created",
            status="completed",
            clear_processing_claim=True,
        )

    @pytest.mark.asyncio
    async def test_cancellation_marker_repairs_pre_run_envelope(self):
        from jobs.stale_task_checker import (
            StaleOrchestrationRunRecoveryDeps,
            StaleRecoveryDeps,
            StaleTaskChecker,
            StaleTaskCheckerDeps,
        )

        message = SimpleNamespace(
            message_id="message-1",
            room_id="room-1",
            extend_info={"orchestration_status": "created"},
            processing_claimed_at=utcnow() - timedelta(minutes=10),
        )
        store = SimpleNamespace(
            get_stale_claimed_orchestration_messages=AsyncMock(return_value=[message]),
            is_message_cancelled=AsyncMock(return_value=True),
            update_orchestration_projection_if_status=AsyncMock(return_value=True),
        )
        run_store = SimpleNamespace(
            get_latest_by_user_message_id=AsyncMock(return_value=None)
        )
        checker = StaleTaskChecker(orphan_threshold_minutes=2)
        checker.set_runtime_deps(
            StaleTaskCheckerDeps(
                store=store,
                rooms_collection=None,
                notify_task_update=AsyncMock(),
                increment_counter=MagicMock(),
                a2a_service=SimpleNamespace(),
            )
        )
        checker.set_execution_recovery_deps(
            StaleRecoveryDeps(schedule_recovery=MagicMock())
        )
        checker.set_orchestration_run_recovery_deps(
            StaleOrchestrationRunRecoveryDeps(orchestration_run_store=run_store)
        )

        await checker._recover_claimed_orchestration_envelopes()

        store.update_orchestration_projection_if_status.assert_awaited_once_with(
            "message-1",
            expected_status="created",
            status="canceled",
            clear_processing_claim=True,
        )

    @pytest.mark.asyncio
    async def test_existing_runs_cannot_starve_later_bootstrap_envelope(self):
        from jobs.stale_task_checker import (
            StaleOrchestrationRunRecoveryDeps,
            StaleRecoveryDeps,
            StaleTaskChecker,
            StaleTaskCheckerDeps,
        )
        from models.orchestration import OrchestrationStatus

        messages = [
            SimpleNamespace(
                message_id=f"existing-{index:03d}",
                room_id="room-1",
                extend_info={"orchestration_status": "created"},
                processing_claimed_at=utcnow() - timedelta(minutes=10),
            )
            for index in range(100)
        ]
        messages.append(
            SimpleNamespace(
                message_id="stranded-101",
                room_id="room-1",
                extend_info={"orchestration_status": "created"},
                processing_claimed_at=None,
            )
        )

        async def scan(_minutes, *, limit, after_message_id):
            page = [
                message
                for message in messages
                if after_message_id is None or message.message_id > after_message_id
            ]
            return page[:limit]

        store = SimpleNamespace(
            get_stale_claimed_orchestration_messages=AsyncMock(side_effect=scan),
            is_message_cancelled=AsyncMock(return_value=False),
            update_room_user_message_by_message_id=AsyncMock(return_value=True),
        )

        async def get_run(message_id):
            if message_id.startswith("existing-"):
                return SimpleNamespace(status=OrchestrationStatus.CREATED)
            return None

        run_store = SimpleNamespace(
            get_latest_by_user_message_id=AsyncMock(side_effect=get_run)
        )
        scheduled = []

        def schedule_recovery(request, *, reason):
            scheduled.append((request, reason))
            return MagicMock(add_done_callback=MagicMock())

        checker = StaleTaskChecker(orphan_threshold_minutes=2)
        checker.set_runtime_deps(
            StaleTaskCheckerDeps(
                store=store,
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

        await checker._recover_claimed_orchestration_envelopes()

        assert store.get_stale_claimed_orchestration_messages.await_count == 2
        assert len(scheduled) == 1
        request, reason = scheduled[0]
        assert request.room_user_message_id == "stranded-101"
        assert reason == "orchestration-envelope"

    @pytest.mark.asyncio
    async def test_recovery_scheduler_releases_slot_on_synchronous_failure(self):
        from jobs.stale_task_checker import (
            MAX_CONCURRENT_RECOVERIES,
            StaleRecoveryDeps,
            StaleTaskChecker,
        )
        from models.request import OrchestrationRequest

        def fail_scheduling(_request, *, reason):
            raise RuntimeError(f"cannot schedule {reason}")

        checker = StaleTaskChecker()
        checker.set_execution_recovery_deps(
            StaleRecoveryDeps(schedule_recovery=fail_scheduling)
        )

        for _ in range(MAX_CONCURRENT_RECOVERIES + 1):
            with pytest.raises(RuntimeError, match="cannot schedule orphan"):
                await checker._schedule_recovery_with_slot(
                    OrchestrationRequest(
                        room_id="room-1",
                        room_user_message_id="message-1",
                    ),
                    reason="orphan",
                )

        assert checker._recovery_semaphore.locked() is False

    @pytest.mark.asyncio
    async def test_pending_cancellations_delegate_to_execution_reconciliation(self):
        from jobs.stale_task_checker import (
            StaleCancellationReconciliationDeps,
            StaleTaskChecker,
        )

        reconciliation = SimpleNamespace(reconcile_pending=AsyncMock(return_value=1))
        checker = StaleTaskChecker(orphan_threshold_minutes=2)
        checker.set_cancellation_reconciliation_deps(
            StaleCancellationReconciliationDeps(reconciliation=reconciliation)
        )

        before = utcnow() - timedelta(minutes=2)
        await checker._reconcile_pending_cancellations()
        after = utcnow() - timedelta(minutes=2)

        reconciliation.reconcile_pending.assert_awaited_once()
        settle_cutoff = reconciliation.reconcile_pending.await_args.kwargs[
            "settle_cutoff"
        ]
        assert before <= settle_cutoff <= after

    @pytest.mark.asyncio
    async def test_cancellation_reconciliation_scan_failure_propagates(self):
        from jobs.stale_task_checker import (
            StaleCancellationReconciliationDeps,
            StaleTaskChecker,
        )

        reconciliation = SimpleNamespace(
            reconcile_pending=AsyncMock(side_effect=RuntimeError("scan failed"))
        )
        checker = StaleTaskChecker()
        checker.set_cancellation_reconciliation_deps(
            StaleCancellationReconciliationDeps(reconciliation=reconciliation)
        )

        with pytest.raises(RuntimeError, match="scan failed"):
            await checker._reconcile_pending_cancellations()

    @pytest.mark.asyncio
    async def test_orchestration_recovery_uses_sidecar_run_store(self):
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
        assert reason == "orchestration"
        assert request.room_id == "room-1"
        assert request.room_user_message_id == "msg-1"

    @pytest.mark.asyncio
    async def test_orchestration_recovery_skips_fresh_processing_claim(
        self,
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
    async def test_orchestration_recovery_fails_closed_without_claim_reader(
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
    async def test_orchestration_recovery_skips_awaiting_user_runs(self):
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
    async def test_orchestration_recovery_recovers_hitl_artifact_creating_runs(
        self,
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
        assert reason == "orchestration"
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

        cancel.assert_awaited_once_with("user-msg-1", failure_reason="failed")


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
