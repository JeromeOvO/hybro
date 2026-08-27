from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from itertools import count
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo.errors import DuplicateKeyError

from common.dto import ExecutionRequest
from execution.facade import ExecutionFacade
from models.response import RoomCenterUserMessageResponse
from models.room import Room
from room import RoomFacade
from room.compat.runtime import RoomServices
from room.repository import MessageMongoRepository
from room.user_message_persistence import UserMessageCommitService


class _RacingUniqueCollection:
    """Force both inserts to race, then atomically choose one winner."""

    def __init__(self) -> None:
        self.docs: list[dict] = []
        self._entered = 0
        self._both_entered = asyncio.Event()
        self._lock = asyncio.Lock()

    async def find_one(self, query: dict, **_kwargs):
        for document in self.docs:
            if all(document.get(key) == value for key, value in query.items()):
                return deepcopy(document)
        return None

    async def insert_one(self, document: dict) -> str:
        self._entered += 1
        if self._entered >= 2:
            self._both_entered.set()
        await self._both_entered.wait()
        async with self._lock:
            if any(
                row.get("message_id") == document.get("message_id")
                or (
                    row.get("room_id") == document.get("room_id")
                    and row.get("client_request_id")
                    == document.get("client_request_id")
                )
                for row in self.docs
            ):
                raise DuplicateKeyError("duplicate unique user-message key")
            self.docs.append(deepcopy(document))
            return f"inserted-{len(self.docs)}"


class _UnusedCollection:
    async def find_one(self, _query: dict, **_kwargs):
        return None


class _Mongo:
    def __init__(self, user_messages: _RacingUniqueCollection) -> None:
        self.user_messages = user_messages

    def collection(self, name: str):
        if name == "room_user_messages":
            return self.user_messages
        return _UnusedCollection()


class _RoomFiles:
    def __init__(self) -> None:
        self.claims: list[str] = []
        self.commits: list[str] = []
        self.releases: list[str] = []

    @asynccontextmanager
    async def write_lease(self, _room_id: str, _owner: str):
        yield "lease-1"

    async def claim_references(
        self,
        *,
        room_id: str,
        owner_id: str,
        message_id: str,
        file_ids: list[str],
    ) -> None:
        self.claims.append(message_id)

    async def commit_references(
        self,
        *,
        message_id: str,
        file_ids: list[str],
    ) -> None:
        self.commits.append(message_id)

    async def release_references(
        self,
        *,
        message_id: str,
        file_ids: list[str],
    ) -> None:
        self.releases.append(message_id)


class _InternalEventPublisher:
    def __init__(self) -> None:
        self.internal_events = []

    async def publish(
        self,
        event,
        *,
        wait_for_handlers: bool = False,
        fanout: bool = True,
    ) -> None:
        self.internal_events.append(event)


class _DeliveryEventPublisher:
    def __init__(self) -> None:
        self.public_events = []

    async def emit(self, event) -> None:
        self.public_events.append(event)


def _execution_request(message_text: str = "hello") -> ExecutionRequest:
    return ExecutionRequest(
        room_id="room-1",
        sender_id="user-1",
        sender_name="User",
        client_request_id="request-1",
        message={
            "room_id": "room-1",
            "message_id": "same-client-supplied-message-id",
            "message_type": "user",
            "user_id": "user-1",
            "message_content": {"message_text": message_text},
        },
        attachments=[{"file_id": "file-1"}],
        mode="direct",
        agent_scope={"source": "all_agents"},
    )


@pytest.mark.asyncio
async def test_concurrent_send_requests_create_one_message_and_one_effect_chain():
    user_messages = _RacingUniqueCollection()
    message_repository = MessageMongoRepository(mongo=_Mongo(user_messages))
    ids = count(1)
    room_facade = RoomFacade(
        repository=MagicMock(),
        message_repository=message_repository,
        agent_registry=MagicMock(),
        membership_source=MagicMock(),
        id_factory=lambda: f"message-{next(ids)}",
        now=MagicMock(),
    )

    room_services = RoomServices()
    room_services.bind_facade(room_facade)
    room_services.bind_store(
        SimpleNamespace(
            get_room_by_room_id=AsyncMock(
                return_value=Room(
                    room_id="room-1",
                    room_name="Room",
                    room_owner_id="user-1",
                    room_owner_name="User",
                    room_agent_set={},
                    extend_info={},
                )
            )
        )
    )
    room_services.cancellation_control = SimpleNamespace(
        create_token=MagicMock(return_value=object()),
        release_token=MagicMock(return_value=True),
        check_cancelled=AsyncMock(return_value=False),
    )
    room_files = _RoomFiles()
    room_services.bind_room_files(room_files)
    room_services.bind_attachment_metadata_reader(
        SimpleNamespace(
            get_for_room_file=AsyncMock(
                return_value={
                    "file_id": "file-1",
                    "room_id": "room-1",
                    "owner_id": "user-1",
                    "mime_type": "text/plain",
                    "file_name": "note.txt",
                    "size_bytes": 10,
                    "sha256": "hash-1",
                    "content_url": "https://files.example/file-1",
                }
            )
        )
    )
    internal_publisher = _InternalEventPublisher()
    delivery_publisher = _DeliveryEventPublisher()
    room_services.bind_user_message_commit(
        UserMessageCommitService(
            writer=room_facade,
            files=room_files,
            internal_event_publisher=internal_publisher,
        )
    )
    preflight_count = 0

    async def preflight(context):
        nonlocal preflight_count
        preflight_count += 1
        message_id = context.user_message.message_id
        return RoomCenterUserMessageResponse(
            room_id="room-1",
            message_id=message_id,
            dispatch_root_message_id=message_id,
            success=True,
            status_code=200,
            preflight_outcome="ready",
        )

    room_services.run_message_preflight_to_room = AsyncMock(side_effect=preflight)
    orchestrator_router = SimpleNamespace(process_room_user_message=AsyncMock())
    run_lifecycle = SimpleNamespace(record_processing_status=AsyncMock())
    engine = ExecutionFacade(
        room_center=room_services,
        orchestrator_router=orchestrator_router,
        hitl_manager=SimpleNamespace(get_pending_requests=AsyncMock(return_value=[])),
        run_lifecycle=run_lifecycle,
        run_reader=SimpleNamespace(
            get_run=AsyncMock(return_value=None),
            get_runs_for_room=AsyncMock(return_value=[]),
        ),
        cancellation_state=SimpleNamespace(
            cancel_message_and_broadcast=AsyncMock(),
            get_active_token=MagicMock(return_value=None),
            release_active_token=MagicMock(return_value=True),
            clear_cancellation=MagicMock(),
        ),
        cancellation_repository=SimpleNamespace(
            request=AsyncMock(return_value=True),
            mark_reconciled=AsyncMock(),
        ),
        cancellation_message_reader=AsyncMock(return_value=None),
        hitl_message_cancellation=SimpleNamespace(
            cancel_requests_for_message=AsyncMock(),
        ),
        agent_task_cleanup=SimpleNamespace(
            cleanup_cancelled_message_tasks=AsyncMock(),
        ),
        event_publisher=delivery_publisher,
        run_event_enabled=lambda: False,
        client_request_id_resolver=SimpleNamespace(
            resolve_client_request_id=AsyncMock(
                side_effect=lambda _message_id, provided: provided
            )
        ),
    )

    first_ack, second_ack = await asyncio.gather(
        engine.execute(_execution_request()),
        engine.execute(_execution_request()),
    )
    await asyncio.gather(
        engine.start_orchestration(_execution_request(), first_ack),
        engine.start_orchestration(_execution_request(), second_ack),
    )

    assert len(user_messages.docs) == 1
    assert {first_ack.message_id, second_ack.message_id} == {
        user_messages.docs[0]["message_id"]
    }
    assert sorted(
        (first_ack.should_start_orchestration, second_ack.should_start_orchestration)
    ) == [False, True]
    assert preflight_count == 1
    assert room_services.run_message_preflight_to_room.await_count == 1
    assert orchestrator_router.process_room_user_message.await_count == 1
    assert len(internal_publisher.internal_events) == 1
    assert run_lifecycle.record_processing_status.await_count == 0
    assert len(delivery_publisher.public_events) == 1
    assert room_services.cancellation_control.create_token.call_count == 1
    assert len(room_files.claims) == 2
    assert len(set(room_files.claims)) == 2
    assert "same-client-supplied-message-id" not in room_files.claims
    assert room_files.commits == [user_messages.docs[0]["message_id"]]
    assert len(room_files.releases) == 1
    assert room_files.releases[0] != room_files.commits[0]

    side_effect_counts = (
        len(user_messages.docs),
        preflight_count,
        len(internal_publisher.internal_events),
        run_lifecycle.record_processing_status.await_count,
        orchestrator_router.process_room_user_message.await_count,
        len(room_files.claims),
        len(room_files.commits),
        len(room_files.releases),
    )
    conflict = await engine.execute(_execution_request("different text"))

    assert conflict.success is False
    assert conflict.status_code == 409
    assert conflict.should_start_orchestration is False
    assert (
        len(user_messages.docs),
        preflight_count,
        len(internal_publisher.internal_events),
        run_lifecycle.record_processing_status.await_count,
        orchestrator_router.process_room_user_message.await_count,
        len(room_files.claims),
        len(room_files.commits),
        len(room_files.releases),
    ) == side_effect_counts
