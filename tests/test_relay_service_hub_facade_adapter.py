from __future__ import annotations

import pytest

from app_shell.relay_service import RelayService


class _DatabaseService:
    pass


class _SSEManager:
    pass


class _OfflineFailurePort:
    async def mark_hub_message_failed(self, command) -> None:
        pass


class _Mongo:
    async def get_hub(self, hub_id: str):
        return {"hub_id": hub_id, "user_id": "owner-1"}


class _StaleHub:
    def __init__(self, hub_id: str, connection_id: str | None = None) -> None:
        self.hub_id = hub_id
        self.connection_id = connection_id


class _FacadeSpy:
    def __init__(self) -> None:
        self.bound_streams = None
        self.bound_writer = None
        self.swept = False
        self.stale_hubs: list[_StaleHub] = []
        self.bound_response_sink = None
        self.worker_id = "worker-1"
        self.task_ownership_store = object()
        self.ownership_maintainer = object()
        self.synced = False
        self.sync_args = None
        self.sync_kwargs = None
        self.bound_leader = None
        self.heartbeat_iterations = 0

    def bind_streams(self, streams):
        self.bound_streams = streams

    def bind_leader_elector(self, leader):
        self.bound_leader = leader

    def bind_agent_registry_writer(self, writer):
        self.bound_writer = writer

    async def sweep_stream_liveness(self) -> list[_StaleHub]:
        self.swept = True
        return list(self.stale_hubs)

    async def run_heartbeat_iteration(self) -> None:
        self.heartbeat_iterations += 1

    def bind_internal_response_sink(self, sink):
        self.bound_response_sink = sink
        return object()

    async def sync_agents(self, *args, **kwargs):
        self.synced = True
        self.sync_args = args
        self.sync_kwargs = kwargs
        return [{"agent_id": "stored-1", "local_agent_id": "local-1"}]


@pytest.fixture
def service(monkeypatch):
    relay = RelayService(
        mongo=None,
        legacy_store=_DatabaseService(),
        sse_manager=_SSEManager(),
        offline_failure_port=_OfflineFailurePort(),
    )
    spy = _FacadeSpy()
    relay._facade = spy
    return relay, spy


def test_relay_service_binds_streams_through_hub_facade(service) -> None:
    relay, spy = service
    streams = object()

    relay.set_stream_service(streams)

    assert spy.bound_streams is streams


def test_relay_service_binds_leader_through_hub_facade(service) -> None:
    relay, spy = service
    leader = object()

    relay.set_leader_election(leader)

    assert spy.bound_leader is leader


def test_relay_service_binds_agent_writer_through_hub_facade(service) -> None:
    relay, spy = service
    writer = object()

    relay.bind_agent_registry_writer(writer)

    assert relay._agent_registry_writer is writer
    assert spy.bound_writer is writer


def test_relay_service_reads_ownership_accessors_through_hub_facade(service) -> None:
    relay, spy = service

    assert relay.task_ownership_store is spy.task_ownership_store
    assert relay.ownership_lease_maintainer is spy.ownership_maintainer
    assert relay.worker_id == spy.worker_id


@pytest.mark.asyncio
async def test_relay_service_delegates_stream_liveness_sweep(service) -> None:
    relay, spy = service

    await relay._do_heartbeat_check(stale_threshold=30)

    assert spy.heartbeat_iterations == 1


@pytest.mark.asyncio
async def test_relay_service_sweeps_offline_queues_through_hub_facade(
    service,
) -> None:
    relay, spy = service

    async def sweep_offline_queues():
        spy.swept = True

    spy.sweep_offline_queues = sweep_offline_queues

    await relay.sweep_offline_queues()

    assert spy.swept is True


def test_relay_service_binds_internal_response_sink_through_hub_facade(service) -> None:
    relay, spy = service

    relay.bind_response_handler(object())

    assert spy.bound_response_sink is not None
    assert relay._internal_response_dispatcher is not None


@pytest.mark.asyncio
async def test_relay_service_delegates_agent_sync_through_hub_facade(service) -> None:
    relay, spy = service
    relay._mongo = _Mongo()
    relay._agent_registry_writer = object()
    api_key = type("APIKey", (), {"user_id": "owner-1"})()

    result = await relay.sync_agents(
        "hub-1",
        [],
        api_key,
        prune_missing=False,
    )

    assert result == [{"agent_id": "stored-1", "local_agent_id": "local-1"}]
    assert spy.synced is True
    assert spy.sync_args == ("hub-1", [], "owner-1")
    assert spy.sync_kwargs == {"prune_missing": False}
