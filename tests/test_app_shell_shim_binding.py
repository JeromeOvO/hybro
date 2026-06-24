from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app_shell import relay_service as relay_module
from app_shell.a2a_runtime import A2AService
from app_shell.context_assembly_service import ContextAssemblyService
from app_shell.relay_service import RelayService, init_relay_service
from app_shell.room_runtime import AppShellRoomCenter, RoomServices
from common.dto import RoomInfo
from models.request import RoomCenterRoomSettingRequest
from room.compat.unbound import (
    UNBOUND_A2A_SERVICE,
    UNBOUND_AGENT_SELECTION_SERVICE,
    UNBOUND_AGENT_SERVICE,
    UNBOUND_DELIVERY_MANAGER,
    UNBOUND_ROOM_MEMORY_SERVICE,
    UNBOUND_TASK_SERVICE,
)


@pytest.mark.asyncio
async def test_room_services_fails_before_facade_bind() -> None:
    service = RoomServices()

    with pytest.raises(
        RuntimeError,
        match=r"RoomServices\.bind_facade\(\) not called - startup incomplete",
    ):
        await service.create_new_room(RoomCenterRoomSettingRequest(room_name="Room"))


def test_room_services_defaults_to_room_owned_unbound_legacy_dependencies() -> None:
    service = RoomServices()

    assert service.agent_service is UNBOUND_AGENT_SERVICE
    assert service.agent_selection_service is UNBOUND_AGENT_SELECTION_SERVICE
    assert service.a2a_service is UNBOUND_A2A_SERVICE
    assert service.room_memory_service is UNBOUND_ROOM_MEMORY_SERVICE
    assert service.sse_manager is UNBOUND_DELIVERY_MANAGER
    assert service.task_service is UNBOUND_TASK_SERVICE


def test_room_services_bind_legacy_dependencies_replaces_unbound_defaults() -> None:
    service = RoomServices()
    deps = {
        "agent_service": object(),
        "agent_selection_service": object(),
        "a2a_service": object(),
        "room_memory_service": object(),
        "sse_manager": object(),
        "task_service": object(),
    }

    service.bind_legacy_dependencies(**deps)

    assert service.agent_service is deps["agent_service"]
    assert service.agent_selection_service is deps["agent_selection_service"]
    assert service.a2a_service is deps["a2a_service"]
    assert service.room_memory_service is deps["room_memory_service"]
    assert service.sse_manager is deps["sse_manager"]
    assert service.task_service is deps["task_service"]


@pytest.mark.asyncio
async def test_room_services_delegates_after_facade_bind() -> None:
    service = RoomServices()
    facade = AsyncMock()
    facade.create_room.return_value = RoomInfo(
        room_id="room-1",
        room_name="Room",
        owner_id="owner-1",
        owner_name="Owner",
    )

    service.bind_facade(facade)

    response = await service.create_new_room(
        RoomCenterRoomSettingRequest(
            room_name="Room",
            room_owner_id="owner-1",
            room_owner_name="Owner",
        )
    )

    assert response.success is True
    assert response.room_id == "room-1"
    facade.create_room.assert_awaited_once()


@pytest.mark.asyncio
async def test_room_center_fails_before_bound_room_services() -> None:
    center = AppShellRoomCenter(room_services=None)

    with pytest.raises(
        RuntimeError,
        match=r"RoomCenter\.bind_facade\(\) not called - startup incomplete",
    ):
        await center.create_new_room(MagicMock())


@pytest.mark.asyncio
async def test_room_center_delegates_after_room_services_bind() -> None:
    room_services = SimpleNamespace(
        _bound=True,
        create_new_room=AsyncMock(return_value="created"),
    )
    center = AppShellRoomCenter(room_services=room_services)

    assert await center.create_new_room(MagicMock()) == "created"
    room_services.create_new_room.assert_awaited_once()


@pytest.mark.asyncio
async def test_a2a_service_fails_before_task_store_bind() -> None:
    service = A2AService()

    with pytest.raises(
        RuntimeError,
        match=r"A2AService\.bind_task_db\(\) not called - startup incomplete",
    ):
        await service.reply_to_task("message-1", "task-1", "ctx-1", "continue")


@pytest.mark.asyncio
async def test_a2a_service_delegates_after_task_store_bind() -> None:
    service = A2AService()
    service.bind_task_db(object())
    tracking = SimpleNamespace(
        create_task_for_tracking=AsyncMock(return_value={"message_id": "message-1"})
    )
    service._task_tracking = tracking
    current_message = object()
    agent_card = object()
    message = object()

    result = await service.create_task_for_tracking(
        current_message=current_message,
        agent_card=agent_card,
        message=message,
        step_number=1,
        total_steps=2,
    )

    assert result == {"message_id": "message-1"}
    tracking.create_task_for_tracking.assert_awaited_once_with(
        current_message,
        agent_card,
        message,
        step_number=1,
        total_steps=2,
    )


def test_context_assembly_service_fails_before_facade_bind() -> None:
    service = ContextAssemblyService()

    with pytest.raises(
        RuntimeError,
        match=(
            r"ContextAssemblyService\.bind_facade\(\) not called - startup incomplete"
        ),
    ):
        service.get_budget_summary()


def test_context_assembly_service_delegates_after_facade_bind() -> None:
    service = ContextAssemblyService()
    facade = SimpleNamespace(get_budget_summary=MagicMock(return_value={"budget": 1}))

    service.bind_facade(facade)

    assert service.get_budget_summary() == {"budget": 1}
    facade.get_budget_summary.assert_called_once_with()


class _RelayFacadeSpy:
    def __init__(self) -> None:
        self.task_ownership_store = object()
        self.ownership_maintainer = object()
        self.worker_id = "worker-1"
        self.started = False
        self.stopped = False
        self.heartbeat_started = False
        self.bound_streams = None
        self.bound_leader = None
        self.bound_writer = None
        self.bound_response_sink = None
        self.pushed_events: list[tuple[str, dict, bool]] = []
        self.cancel_commands: list[object] = []
        self.reply_commands: list[object] = []
        self.dispatch_commands: list[object] = []
        self.swept = False

    def bind_internal_response_sink(self, sink):
        self.bound_response_sink = sink
        return "dispatcher"

    def bind_streams(self, streams):
        self.bound_streams = streams

    def bind_leader_elector(self, leader):
        self.bound_leader = leader

    def bind_agent_registry_writer(self, writer):
        self.bound_writer = writer

    async def start(self) -> None:
        self.started = True

    async def start_heartbeat_monitor(self) -> None:
        self.heartbeat_started = True

    async def stop(self) -> None:
        self.stopped = True

    async def is_hub_online(self, hub_id: str) -> bool:
        self.last_online_hub = hub_id
        return True

    def is_hub_online_cached(self, hub_id: str) -> bool:
        self.last_cached_hub = hub_id
        return False

    async def push_legacy_event_to_hub(
        self,
        hub_id: str,
        payload: dict,
        *,
        mark_agents_offline: bool,
    ) -> bool:
        self.pushed_events.append((hub_id, payload, mark_agents_offline))
        return True

    async def cancel_hub_task(self, command) -> bool:
        self.cancel_commands.append(command)
        return True

    async def reply_to_hub_task(self, command) -> bool:
        self.reply_commands.append(command)
        return True

    async def send_to_hub(self, command):
        self.dispatch_commands.append(command)
        return "dispatched"

    async def sweep_offline_queues(self) -> None:
        self.swept = True


class _RelayLifecycleSpy:
    def __init__(self) -> None:
        self.registered = None
        self.owner_lookup = None
        self.connected = None
        self.heartbeat = None
        self.offline = None
        self.synced = None
        self.published = None
        self.status_user = None
        self._mongo = None
        self._db = None
        self._facade = None

    async def register_hub(self, hub_id, api_key):
        self.registered = (hub_id, api_key)
        return "registered"

    async def get_hub_owner_id(self, hub_id):
        self.owner_lookup = hub_id
        return "owner-1"

    async def connect_hub(self, hub_id, api_key, *, last_event_id=None):
        self.connected = (hub_id, api_key, last_event_id)
        yield {"kind": "connected"}

    async def record_hub_heartbeat(self, hub_id, api_key) -> None:
        self.heartbeat = (hub_id, api_key)

    async def mark_hub_agents_offline(self, hub_id, connection_id=None) -> None:
        self.offline = (hub_id, connection_id)

    async def sync_agents(self, hub_id, agents, api_key, *, prune_missing=True):
        self.synced = (hub_id, agents, api_key, prune_missing)
        return [{"agent_id": "stored-1"}]

    async def process_publish(self, hub_id, request, api_key) -> None:
        self.published = (hub_id, request, api_key)

    async def get_hub_status(self, user_id):
        self.status_user = user_id
        return ["online"]


def _relay_service_with_spies() -> tuple[
    RelayService,
    _RelayFacadeSpy,
    _RelayLifecycleSpy,
]:
    service = RelayService(
        mongo=None,
        legacy_store=object(),
        sse_manager=object(),
        offline_failure_port=object(),
    )
    facade = _RelayFacadeSpy()
    lifecycle = _RelayLifecycleSpy()
    service._facade = facade
    service._lifecycle = lifecycle
    return service, facade, lifecycle


def test_relay_service_fails_fast_for_missing_constructor_dependencies() -> None:
    with pytest.raises(
        ValueError,
        match="RelayService requires a mongo-compatible db/service",
    ):
        RelayService(
            mongo=None,
            legacy_store=None,
            sse_manager=object(),
            offline_failure_port=object(),
        )

    with pytest.raises(
        ValueError,
        match="init_relay_service requires offline_failure_port",
    ):
        init_relay_service(
            mongo=None,
            db=object(),
            sse_manager=object(),
            room_message_center=SimpleNamespace(agent_response_handler=object()),
        )


@pytest.mark.asyncio
async def test_relay_service_facade_methods_delegate_after_constructor_binding() -> None:
    service, facade, _ = _relay_service_with_spies()
    streams = object()
    leader = object()
    writer = object()
    response_handler = object()
    dispatch_command = object()
    event = SimpleNamespace(model_dump=MagicMock(return_value={"payload": 1}))

    service.set_stream_service(streams)
    service.set_leader_election(leader)
    service.bind_agent_registry_writer(writer)
    service.bind_response_handler(response_handler)
    await service.start()
    await service.stop()
    online = await service.is_hub_alive("hub-1")
    cached_online = service.is_hub_alive_cached("hub-1")
    pushed = await service.push_to_hub("hub-1", event)
    canceled = await service.cancel_hub_task(dispatch_command)
    relay_canceled = await service.cancel_relay_task(
        "hub-1",
        "message-1",
        "agent-1",
        task_id="task-1",
    )
    replied = await service.reply_to_hub_task(dispatch_command)
    relay_replied = await service.reply_to_relay_task(
        "hub-1",
        "message-1",
        "agent-1",
        "approved",
        "room-1",
        task_id="task-1",
        context_id="ctx-1",
    )
    dispatched = await service.send_to_hub(dispatch_command)
    await service.sweep_offline_queues()

    assert facade.bound_streams is streams
    assert facade.bound_leader is leader
    assert facade.bound_writer is writer
    assert facade.bound_response_sink is not None
    assert service._internal_response_dispatcher == "dispatcher"
    assert facade.started is True
    assert facade.heartbeat_started is True
    assert facade.stopped is True
    assert online is True
    assert facade.last_online_hub == "hub-1"
    assert cached_online is False
    assert facade.last_cached_hub == "hub-1"
    assert pushed is True
    assert facade.pushed_events == [("hub-1", {"payload": 1}, True)]
    assert canceled is True
    assert facade.cancel_commands[0] is dispatch_command
    assert relay_canceled is True
    assert facade.cancel_commands[1].hub_id == "hub-1"
    assert facade.cancel_commands[1].agent_message_id == "message-1"
    assert replied is True
    assert facade.reply_commands[0] is dispatch_command
    assert relay_replied is True
    assert facade.reply_commands[1].reply_text == "approved"
    assert dispatched == "dispatched"
    assert facade.dispatch_commands == [dispatch_command]
    assert facade.swept is True


@pytest.mark.asyncio
async def test_relay_service_lifecycle_methods_delegate_after_constructor_binding() -> None:
    service, facade, lifecycle = _relay_service_with_spies()
    api_key = object()
    request = object()

    registered = await service.register_hub("hub-1", api_key)
    owner = await service.get_hub_owner_id("hub-1")
    connected_events = [
        event
        async for event in service.connect_hub(
            "hub-1",
            api_key,
            last_event_id="event-1",
        )
    ]
    await service.record_hub_heartbeat("hub-1", api_key)
    await service.mark_hub_agents_offline("hub-1", connection_id="conn-1")
    synced = await service.sync_agents(
        "hub-1",
        ["agent-1"],
        api_key,
        prune_missing=False,
    )
    await service.process_publish("hub-1", request, api_key)
    status = await service.get_hub_status("owner-1")

    assert registered == "registered"
    assert lifecycle.registered == ("hub-1", api_key)
    assert owner == "owner-1"
    assert lifecycle.owner_lookup == "hub-1"
    assert connected_events == [{"kind": "connected"}]
    assert lifecycle.connected == ("hub-1", api_key, "event-1")
    assert lifecycle.heartbeat == ("hub-1", api_key)
    assert lifecycle.offline == ("hub-1", "conn-1")
    assert synced == [{"agent_id": "stored-1"}]
    assert lifecycle.synced == ("hub-1", ["agent-1"], api_key, False)
    assert lifecycle._facade is facade
    assert lifecycle.published == ("hub-1", request, api_key)
    assert status == ["online"]
    assert lifecycle.status_user == "owner-1"


@pytest.mark.asyncio
async def test_relay_hub_liveness_reader_delegates_to_relay_service() -> None:
    service = SimpleNamespace(
        is_hub_alive=AsyncMock(return_value=True),
        get_hub_owner_id=AsyncMock(return_value="owner-1"),
    )
    reader = relay_module.RelayHubLivenessReader(service)

    assert await reader.is_hub_online("hub-1") is True
    assert await reader.get_hub_owner_id("hub-1") == "owner-1"
    service.is_hub_alive.assert_awaited_once_with("hub-1")
    service.get_hub_owner_id.assert_awaited_once_with("hub-1")


def test_init_relay_service_binds_dependencies_on_happy_path() -> None:
    response_handler = SimpleNamespace(handle=AsyncMock())
    processor = SimpleNamespace(bind_relay_service=MagicMock())
    hitl_coordinator = object()

    service = init_relay_service(
        mongo=None,
        db=object(),
        sse_manager=object(),
        room_message_center=SimpleNamespace(
            agent_response_handler=response_handler,
            agent_message_processor=processor,
        ),
        hitl_coordinator=hitl_coordinator,
        offline_failure_port=object(),
    )

    assert isinstance(service, RelayService)
    assert relay_module.relay_service is service
    assert service._response_handler is response_handler
    assert service._internal_response_dispatcher is not None
    assert response_handler.hitl_coordinator is hitl_coordinator
    processor.bind_relay_service.assert_called_once_with(service)
