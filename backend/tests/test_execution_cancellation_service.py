from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from common.dto import CancellationAck
from common.utils.time import utcnow
from execution.cancellation.finalizer import CancellationFinalizationResult
from execution.cancellation.service import CancellationService
from models.orchestration import OrchestrationStatus


def _result(*, reconciled: bool = True, applied: bool = True):
    return CancellationFinalizationResult(
        status=OrchestrationStatus.CANCELED,
        cancellation_applied=applied,
        reconciled=reconciled,
    )


def _service(*, repository=None, finalizer=None, message_reader=None):
    repository = repository or SimpleNamespace(
        request=AsyncMock(return_value=True),
        list_pending=AsyncMock(return_value=[]),
        mark_reconciled=AsyncMock(return_value=True),
    )
    finalizer = finalizer or SimpleNamespace(finalize=AsyncMock(return_value=_result()))
    message_reader = message_reader or AsyncMock(
        return_value=SimpleNamespace(room_id="room-1")
    )
    return (
        CancellationService(
            repository=repository,
            finalizer=finalizer,
            message_reader=message_reader,
        ),
        repository,
        finalizer,
        message_reader,
    )


@pytest.mark.asyncio
async def test_request_cancellation_persists_before_finalizing_and_preserves_ack():
    order = []

    async def request(message_id, user_id):
        order.append(("request", message_id, user_id))
        return True

    async def finalize(**kwargs):
        order.append(("finalize", kwargs["message_id"]))
        return _result(applied=False)

    repository = SimpleNamespace(
        request=AsyncMock(side_effect=request),
        list_pending=AsyncMock(return_value=[]),
        mark_reconciled=AsyncMock(return_value=True),
    )
    service, _, _, _ = _service(
        repository=repository,
        finalizer=SimpleNamespace(finalize=AsyncMock(side_effect=finalize)),
    )

    ack = await service.cancel(
        room_id="room-1",
        message_id="message-1",
        requested_by_user_id="user-1",
    )

    assert isinstance(ack, CancellationAck)
    assert ack.status == "canceled"
    assert ack.cancellation_applied is False
    assert ack.reconciled is True
    assert order == [
        ("request", "message-1", "user-1"),
        ("finalize", "message-1"),
    ]


@pytest.mark.asyncio
async def test_request_cancellation_returns_pending_ack_when_finalization_fails():
    service, repository, _, _ = _service(
        finalizer=SimpleNamespace(
            finalize=AsyncMock(side_effect=RuntimeError("temporary"))
        )
    )

    ack = await service.request_cancellation(
        room_id="room-1",
        message_id="message-1",
        requested_by_user_id="user-1",
    )

    assert isinstance(ack, CancellationAck)
    assert ack.status == "cancellation_pending"
    assert ack.reconciled is False
    repository.request.assert_awaited_once_with("message-1", "user-1")


@pytest.mark.asyncio
async def test_reconcile_pending_pages_marks_missing_and_computes_settle_cutoff():
    now = utcnow()
    markers = {
        None: [
            {"message_id": "a", "cancelled_at": now - timedelta(minutes=10)},
            {"message_id": "b", "cancelled_at": now.isoformat()},
        ],
        "b": [{"message_id": "c", "cancelled_at": now}],
    }

    async def list_pending(*, limit, after_message_id):
        assert limit == 2
        return markers[after_message_id]

    repository = SimpleNamespace(
        request=AsyncMock(return_value=True),
        list_pending=AsyncMock(side_effect=list_pending),
        mark_reconciled=AsyncMock(return_value=True),
    )

    async def read_message(message_id):
        if message_id == "b":
            return None
        return SimpleNamespace(room_id="room-1")

    finalizer = SimpleNamespace(finalize=AsyncMock(return_value=_result()))
    service, _, _, _ = _service(
        repository=repository,
        finalizer=finalizer,
        message_reader=AsyncMock(side_effect=read_message),
    )

    count = await service.reconcile_pending(
        settle_cutoff=now - timedelta(minutes=2),
        batch_size=2,
    )

    assert count == 3
    repository.mark_reconciled.assert_awaited_once_with("b")
    assert [
        call.kwargs["message_id"] for call in finalizer.finalize.await_args_list
    ] == [
        "a",
        "c",
    ]
    assert [
        call.kwargs["settle_no_run"] for call in finalizer.finalize.await_args_list
    ] == [True, False]
    assert [
        call.kwargs["after_message_id"]
        for call in repository.list_pending.await_args_list
    ] == [None, "b"]


@pytest.mark.asyncio
async def test_reconcile_pending_leaves_failed_marker_pending_and_continues():
    repository = SimpleNamespace(
        request=AsyncMock(return_value=True),
        list_pending=AsyncMock(
            return_value=[
                {"message_id": "failed", "cancelled_at": utcnow()},
                {"message_id": "ok", "cancelled_at": utcnow()},
            ]
        ),
        mark_reconciled=AsyncMock(return_value=True),
    )

    async def finalize(**kwargs):
        if kwargs["message_id"] == "failed":
            raise RuntimeError("temporary")
        return _result()

    finalizer = SimpleNamespace(finalize=AsyncMock(side_effect=finalize))
    service, _, _, _ = _service(repository=repository, finalizer=finalizer)

    count = await service.reconcile_pending(settle_cutoff=utcnow())

    assert count == 1
    assert [
        call.kwargs["message_id"] for call in finalizer.finalize.await_args_list
    ] == [
        "failed",
        "ok",
    ]


@pytest.mark.asyncio
async def test_reconcile_pending_retries_marker_on_next_scan():
    marker = {"message_id": "retry", "cancelled_at": utcnow()}
    repository = SimpleNamespace(
        request=AsyncMock(return_value=True),
        list_pending=AsyncMock(return_value=[marker]),
        mark_reconciled=AsyncMock(return_value=True),
    )
    finalizer = SimpleNamespace(
        finalize=AsyncMock(side_effect=[RuntimeError("temporary"), _result()])
    )
    service, _, _, _ = _service(repository=repository, finalizer=finalizer)

    assert await service.reconcile_pending(settle_cutoff=utcnow()) == 0
    assert await service.reconcile_pending(settle_cutoff=utcnow()) == 1
    assert finalizer.finalize.await_count == 2


@pytest.mark.asyncio
async def test_reconcile_pending_propagates_repository_scan_failure():
    service, _, _, _ = _service(
        repository=SimpleNamespace(
            request=AsyncMock(return_value=True),
            list_pending=AsyncMock(side_effect=RuntimeError("scan failed")),
            mark_reconciled=AsyncMock(return_value=True),
        )
    )

    with pytest.raises(RuntimeError, match="scan failed"):
        await service.reconcile_pending(settle_cutoff=utcnow())


@pytest.mark.asyncio
async def test_reconcile_pending_rejects_non_positive_batch_size():
    service, _, _, _ = _service()

    with pytest.raises(ValueError, match="positive"):
        await service.reconcile_pending(settle_cutoff=utcnow(), batch_size=0)
