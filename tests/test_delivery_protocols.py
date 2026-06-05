import ast
import asyncio
import importlib
import inspect
import tomllib
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_delivery_compatibility_uses_only_facade_public_api():
    import inspect

    from delivery.facade import DeliveryCompatibility

    class Facade:
        delivery_kv_connected = True
        delivery_pubsub_connected = True
        redis_connected = True
        broker_connected = True
        change_stream_connected = True
        room_connections = {"room-1": ["conn-1"]}

        def __init__(self) -> None:
            self.calls = []

        async def open_connection(self, room_id: str):
            self.calls.append(("open_connection", room_id))
            return "connection"

        async def remove_connection(self, room_id: str, connection_id: str) -> None:
            self.calls.append(("remove_connection", room_id, connection_id))

        def get_room_status(self, room_id: str) -> dict:
            self.calls.append(("get_room_status", room_id))
            return {"room_id": room_id}

        def is_cancelled(self, message_id: str) -> bool:
            self.calls.append(("is_cancelled", message_id))
            return False

        def cancel_message(self, message_id: str) -> None:
            self.calls.append(("cancel_message", message_id))

        async def cancel_message_and_broadcast(self, message_id: str) -> None:
            self.calls.append(("cancel_message_and_broadcast", message_id))

        async def check_cancelled(self, message_id: str) -> bool:
            self.calls.append(("check_cancelled", message_id))
            return False

        def clear_cancellation(self, message_id: str) -> None:
            self.calls.append(("clear_cancellation", message_id))

        def create_token(self, message_id: str):
            self.calls.append(("create_token", message_id))
            return "token"

        def get_token(self, message_id: str):
            self.calls.append(("get_token", message_id))
            return "token"

        def remove_token(self, message_id: str) -> None:
            self.calls.append(("remove_token", message_id))

        async def start_change_stream_watcher(self) -> None:
            self.calls.append(("start_change_stream_watcher",))

        async def stop_change_stream_watcher(self) -> None:
            self.calls.append(("stop_change_stream_watcher",))

        def set_draining(self, draining: bool) -> None:
            self.calls.append(("set_draining", draining))

        async def refresh_health(self) -> None:
            self.calls.append(("refresh_health",))

    facade = Facade()
    compat = DeliveryCompatibility(facade)

    assert await compat.open_connection("room-1") == "connection"
    await compat.remove_connection("room-1", "conn-1")
    assert compat.get_room_status("room-1") == {"room_id": "room-1"}
    assert compat.room_connections == {"room-1": ["conn-1"]}
    assert compat.is_cancelled("msg-1") is False
    compat.cancel_message("msg-1")
    await compat.cancel_message_and_broadcast("msg-1")
    assert await compat.check_cancelled("msg-1") is False
    compat.clear_cancellation("msg-1")
    assert compat.create_token("msg-1") == "token"
    assert compat.get_token("msg-1") == "token"
    compat.remove_token("msg-1")
    await compat.start_change_stream_watcher()
    await compat.stop_change_stream_watcher()
    compat.set_draining(True)
    assert compat.change_stream_connected is True
    assert compat.delivery_kv_connected is True
    assert compat.delivery_pubsub_connected is True
    await compat.refresh_health()
    assert compat.redis_connected is True
    assert compat.broker_connected is True

    source = inspect.getsource(DeliveryCompatibility)
    assert "._sse_transport" not in source
    assert "._cancellation_watcher" not in source


FORBIDDEN_DELIVERY_ROOTS = {
    "a2a_adapter",
    "agent",
    "api",
    "config",
    "container",
    "context_memory",
    "dal",
    "database",
    "execution",
    "hub_runtime_bridge",
    "infrastructure",
    "jobs",
    "llm_gateway",
    "main",
    "models",
    "modules",
    "platform_module",
    "room",
    "services",
}

PRODUCTION_REVERSE_IMPORT_ROOTS = [
    Path("main.py"),
    Path("a2a_adapter"),
    Path("agent"),
    Path("common"),
    Path("config"),
    Path("context_memory"),
    Path("dal"),
    Path("database"),
    Path("execution"),
    Path("hub_runtime_bridge"),
    Path("infrastructure"),
    Path("jobs"),
    Path("llm_gateway"),
    Path("models"),
    Path("modules"),
    Path("platform_module"),
    Path("room"),
    Path("services"),
    Path("api"),
]


def _python_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.exists():
        return []
    return sorted(p for p in path.rglob("*.py") if "__pycache__" not in p.parts)


def _static_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _attribute_chain(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _attribute_chain(node.value)
        if base:
            return f"{base}.{node.attr}"
    return None


def test_delivery_package_skeleton_and_config_exports():
    delivery = importlib.import_module("delivery")

    from delivery.config import DeliveryConfig, DeliveryStartupPolicy
    from delivery.types import RoomSubscriptionLimitExceeded, TaskRunner

    if getattr(delivery, "__all__", []) != []:
        assert delivery.__all__ == [
            "DeliveryFacade",
            "EventPublisherImpl",
            "SSETransportImpl",
        ]

    config = DeliveryConfig()
    assert config.heartbeat_interval_seconds == 30.0
    assert config.shutdown_drain_seconds == 5.0
    assert config.redis_sse_channel_prefix == "sse:room:"
    assert config.redis_cancel_channel == "cancel:global"
    assert config.redis_internal_channel == "internal:global"
    assert config.redis_dead_letter_channel == "delivery:dead_letter"
    assert config.redis_room_subscription_production_limit == 40
    assert config.redis_subscription_reserved_connections == 10
    assert config.redis_max_connections == 50
    assert config.terminal_processing_statuses == frozenset(
        {"completed", "failed", "canceled", "rejected", "rate_limited", "error"}
    )

    policy = DeliveryStartupPolicy(redis_expected=True, multi_worker=True)
    assert policy.redis_expected is True
    assert policy.multi_worker is True
    assert policy.allow_degraded_change_stream is False

    assert issubclass(RoomSubscriptionLimitExceeded, RuntimeError)
    assert callable(TaskRunner)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("heartbeat_interval_seconds", 0),
        ("shutdown_drain_seconds", 0),
        ("cancellation_ttl_seconds", 0),
        ("terminal_dedup_ttl_seconds", 0),
        ("cancellation_cache_maxsize", 0),
        ("cancellation_token_cache_maxsize", 0),
        ("terminal_dedup_cache_maxsize", 0),
        ("dead_letter_memory_maxlen", 0),
        ("handler_shutdown_timeout_seconds", 0),
        ("redis_reconnect_delay", 0),
        ("redis_reconnect_max_delay", 0),
        ("redis_max_connections", 0),
        ("redis_subscription_reserved_connections", 0),
        ("redis_room_subscription_production_limit", 0),
        ("cs_backoff_base", 0),
        ("cs_backoff_max", 0),
        ("cs_backoff_factor", 0.5),
        ("redis_sse_channel_prefix", ""),
        ("redis_cancel_channel", ""),
        ("redis_internal_channel", ""),
        ("redis_dead_letter_channel", ""),
        ("redis_cancel_key_prefix", ""),
        ("redis_terminal_key_prefix", ""),
    ],
)
def test_delivery_config_rejects_invalid_values(field, value):
    from delivery.config import DeliveryConfig

    with pytest.raises(ValueError):
        DeliveryConfig(**{field: value})


@pytest.mark.parametrize(
    "kwargs",
    [
        {"redis_reconnect_delay": 2.0, "redis_reconnect_max_delay": 1.0},
        {"cs_backoff_base": 2.0, "cs_backoff_max": 1.0},
        {"cs_jitter_fraction": -0.1},
        {"cs_jitter_fraction": 1.1},
        {
            "redis_max_connections": 50,
            "redis_subscription_reserved_connections": 10,
            "redis_room_subscription_production_limit": 100,
        },
        {"terminal_processing_statuses": "failed,completed"},
        {"terminal_processing_statuses": frozenset()},
        {"terminal_processing_statuses": frozenset({" "})},
        {"terminal_processing_statuses": frozenset({"failed", 1})},
    ],
)
def test_delivery_config_rejects_invalid_combinations(kwargs):
    from delivery.config import DeliveryConfig

    with pytest.raises(ValueError):
        DeliveryConfig(**kwargs)


def test_delivery_config_normalizes_terminal_statuses():
    from delivery.config import DeliveryConfig

    config = DeliveryConfig(terminal_processing_statuses={" Completed ", "FAILED"})

    assert config.terminal_processing_statuses == frozenset({"completed", "failed"})


def test_delivery_startup_policy_rejects_invalid_degraded_combinations():
    from delivery.config import DeliveryStartupPolicy

    with pytest.raises(ValueError):
        DeliveryStartupPolicy(
            redis_expected=True,
            multi_worker=False,
            allow_degraded_change_stream=True,
        )

    with pytest.raises(ValueError):
        DeliveryStartupPolicy(
            redis_expected=False,
            multi_worker=True,
            allow_degraded_change_stream=True,
        )


def test_common_settings_expose_delivery_config_fields():
    from common.config.settings import Settings
    from delivery.config import DeliveryConfig

    settings = Settings(_env_file=None)
    values = {
        field: getattr(settings, field)
        for field in DeliveryConfig.__dataclass_fields__
    }

    assert DeliveryConfig(**values) == DeliveryConfig()

    custom = Settings(
        _env_file=None,
        terminal_processing_statuses="Done, FAILED",
    )
    assert custom.terminal_processing_statuses == frozenset({"done", "failed"})


def test_delivery_packages_are_registered():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    packages = set(pyproject["tool"]["setuptools"]["packages"])

    assert {"delivery", "delivery.sse", "delivery.event_bus"}.issubset(packages)


def test_sse_transport_connect_is_sync_async_iterator_factory():
    from common.protocols import SSETransport

    assert inspect.iscoroutinefunction(SSETransport.connect) is False


def test_delivery_facade_exports_and_protocol_conformance():
    import delivery
    from common.protocols import EventPublisher, SSETransport
    from delivery import DeliveryFacade, EventPublisherImpl, SSETransportImpl

    assert delivery.__all__ == [
        "DeliveryFacade",
        "EventPublisherImpl",
        "SSETransportImpl",
    ]
    assert isinstance(EventPublisherImpl, type)
    assert isinstance(SSETransportImpl, type)
    assert DeliveryFacade is delivery.DeliveryFacade
    assert isinstance(EventPublisherImpl.__new__(EventPublisherImpl), EventPublisher)
    assert isinstance(SSETransportImpl.__new__(SSETransportImpl), SSETransport)


def test_tracing_helpers_preserve_explicit_trace_context():
    from common.observability import (
        get_current_trace_id,
        trace_id_context,
        traced_create_task,
    )

    async def read_trace_id():
        await asyncio.sleep(0)
        return get_current_trace_id()

    async def run():
        assert get_current_trace_id() is None
        with trace_id_context("trace-123"):
            task = traced_create_task(read_trace_id(), name="delivery-test-task")
            assert task.get_name() == "delivery-test-task"
            assert await task == "trace-123"
        assert get_current_trace_id() is None

    asyncio.run(run())


def test_delivery_import_boundary():
    delivery_root = Path("delivery")
    assert delivery_root.exists()

    for path in _python_files(delivery_root):
        tree = ast.parse(path.read_text(), filename=str(path))
        imported_common_config_aliases: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root not in FORBIDDEN_DELIVERY_ROOTS, path
                    if alias.name in {"common.config", "common.config.settings"}:
                        imported_common_config_aliases.add(alias.asname or alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".")[0]
                assert root not in FORBIDDEN_DELIVERY_ROOTS, path
                if node.module in {"common.config", "common.config.settings"}:
                    imported = {alias.name for alias in node.names}
                    assert "settings" not in imported, path
            elif isinstance(node, ast.Call):
                call_name = _attribute_chain(node.func)
                if call_name in {"importlib.import_module", "__import__"} and node.args:
                    target = _static_string(node.args[0])
                    if target:
                        root = target.split(".")[0]
                        assert root not in FORBIDDEN_DELIVERY_ROOTS, path
                        assert target not in {"common.config", "common.config.settings"}, path
            elif isinstance(node, ast.Attribute) and node.attr == "settings":
                chain = _attribute_chain(node.value)
                assert chain not in imported_common_config_aliases, path
                assert chain not in {"common.config", "common.config.settings"}, path


def test_business_modules_do_not_import_delivery_concretes():
    for root in PRODUCTION_REVERSE_IMPORT_ROOTS:
        for path in _python_files(root):
            if path == Path("container.py"):
                continue
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported = {alias.name.split(".")[0] for alias in node.names}
                    assert "delivery" not in imported, path
                elif isinstance(node, ast.ImportFrom) and node.module:
                    assert node.module.split(".")[0] != "delivery", path
                elif isinstance(node, ast.Call):
                    call_name = _attribute_chain(node.func)
                    if call_name in {"importlib.import_module", "__import__"} and node.args:
                        target = _static_string(node.args[0])
                        assert not (target and target.split(".")[0] == "delivery"), path


def test_delivery_background_tasks_use_traced_task_runner():
    for path in _python_files(Path("delivery")):
        tree = ast.parse(path.read_text(), filename=str(path))
        asyncio_aliases = {"asyncio"}
        create_task_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "asyncio":
                        asyncio_aliases.add(alias.asname or alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module == "asyncio":
                for alias in node.names:
                    if alias.name == "create_task":
                        create_task_names.add(alias.asname or alias.name)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                assert node.func.id not in create_task_names, path
            if isinstance(node.func, ast.Attribute) and node.func.attr == "create_task":
                owner = _attribute_chain(node.func.value)
                assert owner not in asyncio_aliases, path
                assert owner != "loop", path


class _LifecycleComponent:
    def __init__(self, name: str, calls: list[str]):
        self.name = name
        self.calls = calls
        self.started = False
        self.stopped = False

    async def start(self):
        self.calls.append(f"{self.name}.start")
        self.started = True

    async def stop(self):
        self.calls.append(f"{self.name}.stop")
        self.stopped = True


class _FakeTransport(_LifecycleComponent):
    def __init__(self, calls: list[str]):
        super().__init__("transport", calls)
        self.closed = False
        self.draining = False
        self.compat_calls: list[tuple] = []

    async def start_cancellation_watcher(self):
        self.calls.append("transport.start_cancellation_watcher")

    async def stop_cancellation_watcher(self):
        self.calls.append("transport.stop_cancellation_watcher")

    async def close_all_connections(self):
        self.calls.append("transport.close_all_connections")
        self.closed = True

    def set_draining(self, draining: bool):
        self.draining = draining

    async def open_connection(self, room_id: str):
        self.compat_calls.append(("open_connection", room_id))
        return "connection"

    async def remove_connection(self, room_id: str, connection_id: str):
        self.compat_calls.append(("remove_connection", room_id, connection_id))

    def get_room_status(self, room_id: str):
        return {"room_id": room_id}

    def is_cancelled(self, message_id: str):
        return False

    def cancel_message(self, message_id: str):
        self.compat_calls.append(("cancel_message", message_id))

    async def cancel_message_and_broadcast(self, message_id: str):
        self.compat_calls.append(("cancel_message_and_broadcast", message_id))

    async def check_cancelled(self, message_id: str):
        return False

    def clear_cancellation(self, message_id: str):
        self.compat_calls.append(("clear_cancellation", message_id))

    def create_token(self, message_id: str):
        return None

    def get_token(self, message_id: str):
        return None

    def remove_token(self, message_id: str):
        self.compat_calls.append(("remove_token", message_id))


class _FakePublisher(_LifecycleComponent):
    def __init__(self, calls: list[str]):
        super().__init__("publisher", calls)


class _FakeBus(_LifecycleComponent):
    def __init__(self, calls: list[str], connected=True):
        super().__init__("bus", calls)
        self.connected = connected
        self.health_refreshed = False

    @property
    def is_connected(self):
        return self.connected

    async def refresh_health(self):
        self.health_refreshed = True


class _FakeRedisKV:
    def __init__(self, result=True):
        self.result = result
        self.closed = 0

    async def ping(self):
        return self.result

    async def close(self):
        self.closed += 1


def _make_facade(redis_kv=None, bus_connected=True):
    from delivery.config import DeliveryConfig, DeliveryStartupPolicy
    from delivery.facade import DeliveryFacade

    calls: list[str] = []
    publisher = _FakePublisher(calls)
    transport = _FakeTransport(calls)
    bus = _FakeBus(calls, connected=bus_connected)
    facade = DeliveryFacade(
        event_publisher=publisher,
        sse_transport=transport,
        event_bus=bus,
        cancellation_watcher=None,
        redis_kv=redis_kv,
        config=DeliveryConfig(),
        startup_policy=DeliveryStartupPolicy(redis_expected=False, multi_worker=False),
        instance_id="worker-1",
    )
    return facade, publisher, transport, bus, calls


@pytest.mark.asyncio
async def test_delivery_facade_lifecycle_health_and_compatibility():
    redis_kv = _FakeRedisKV(result=True)
    facade, publisher, transport, bus, calls = _make_facade(redis_kv=redis_kv)

    await facade.start()
    assert calls == [
        "transport.start_cancellation_watcher",
        "bus.start",
        "publisher.start",
    ]
    assert facade.instance_id == "worker-1"
    assert facade.delivery_kv_connected is True
    assert facade.delivery_pubsub_connected is True
    assert facade.redis_connected is True
    assert facade.broker_connected is True

    assert await facade.compat.open_connection("room-1") == "connection"
    assert transport.compat_calls == [("open_connection", "room-1")]

    facade.set_draining(True)
    assert transport.draining is True

    await facade.stop()
    assert calls[-4:] == [
        "publisher.stop",
        "transport.close_all_connections",
        "bus.stop",
        "transport.stop_cancellation_watcher",
    ]
    assert redis_kv.closed == 1


@pytest.mark.asyncio
async def test_delivery_facade_refresh_health_handles_ping_failure():
    redis_kv = _FakeRedisKV(result=False)
    facade, _, _, bus, _ = _make_facade(redis_kv=redis_kv, bus_connected=False)

    await facade.refresh_health()

    assert bus.health_refreshed is True
    assert facade.delivery_kv_connected is False
    assert facade.delivery_pubsub_connected is False


def test_container_delivery_factories_and_config_mapping():
    from common.protocols import EventPublisher, SSETransport
    from container import (
        DeliveryDeps,
        create_delivery_config,
        create_delivery_deps,
        create_delivery_facade,
        create_delivery_redis_clients,
        create_delivery_startup_policy,
    )
    from delivery.config import DeliveryConfig, DeliveryStartupPolicy
    from delivery.facade import DeliveryFacade

    values = {
        field: getattr(DeliveryConfig(), field)
        for field in DeliveryConfig.__dataclass_fields__
    }
    values.update(
        {
            "heartbeat_interval_seconds": 2.5,
            "shutdown_drain_seconds": 1.25,
            "cancellation_ttl_seconds": 42,
            "terminal_dedup_ttl_seconds": 43,
            "cancellation_cache_maxsize": 44,
            "cancellation_token_cache_maxsize": 45,
            "terminal_dedup_cache_maxsize": 46,
            "redis_sse_channel_prefix": "custom:sse:",
            "redis_cancel_channel": "custom:cancel",
            "redis_internal_channel": "custom:internal",
            "redis_dead_letter_channel": "custom:dead",
            "redis_cancel_key_prefix": "custom:cancelled:",
            "redis_terminal_key_prefix": "custom:terminal:",
            "dead_letter_memory_maxlen": 47,
            "handler_shutdown_timeout_seconds": 0.5,
            "redis_reconnect_delay": 0.25,
            "redis_reconnect_max_delay": 3.0,
            "redis_max_connections": 120,
            "redis_subscription_reserved_connections": 10,
            "redis_room_subscription_production_limit": 100,
            "cs_backoff_base": 0.5,
            "cs_backoff_max": 8.0,
            "cs_backoff_factor": 1.5,
            "cs_jitter_fraction": 0.1,
            "terminal_processing_statuses": "Done, FAILED",
        }
    )
    config = create_delivery_config(SimpleNamespace(**values))

    assert config.heartbeat_interval_seconds == 2.5
    assert config.redis_sse_channel_prefix == "custom:sse:"
    assert config.redis_internal_channel == "custom:internal"
    assert config.redis_dead_letter_channel == "custom:dead"
    assert config.redis_max_connections == 120
    assert config.redis_room_subscription_production_limit == 100
    assert config.terminal_processing_statuses == frozenset({"done", "failed"})

    redis_kv, redis_pubsub = create_delivery_redis_clients(
        redis_url="redis://localhost:6379/0",
        config=config,
    )
    assert redis_kv is not None
    assert redis_pubsub is not None
    assert redis_pubsub.max_connections == 120
    assert create_delivery_redis_clients(redis_url="", config=config) == (None, None)

    policy = create_delivery_startup_policy(redis_url="", multi_worker=False)
    assert policy == DeliveryStartupPolicy(
        redis_expected=False,
        multi_worker=False,
        allow_degraded_change_stream=True,
    )
    fatal_policy = create_delivery_startup_policy(
        redis_url="redis://localhost:6379/0",
        multi_worker=True,
    )
    assert fatal_policy == DeliveryStartupPolicy(
        redis_expected=True,
        multi_worker=True,
        allow_degraded_change_stream=False,
    )

    class FakeCollection:
        pass

    facade = create_delivery_facade(
        cancellation_collection=FakeCollection(),
        startup_policy=fatal_policy,
        redis_kv=None,
        redis_pubsub=None,
        config=DeliveryConfig(),
        instance_id="worker-1",
        id_factory=lambda: "conn-1",
    )
    deps = create_delivery_deps(facade)

    assert isinstance(facade, DeliveryFacade)
    assert isinstance(deps, DeliveryDeps)
    assert [field.name for field in fields(DeliveryDeps)] == [
        "event_publisher",
        "sse_transport",
    ]
    assert deps.event_publisher is facade.event_publisher
    assert deps.sse_transport is facade.sse_transport
    assert isinstance(deps.event_publisher, EventPublisher)
    assert isinstance(deps.sse_transport, SSETransport)


def test_main_does_not_import_or_instantiate_concrete_dal():
    forbidden_names = {
        "MongoDALImpl",
        "VectorDALImpl",
        "RedisKVImpl",
        "RedisPubSubImpl",
    }
    tree = ast.parse(Path("main.py").read_text(), filename="main.py")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] != "dal"
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] != "dal"
        elif isinstance(node, ast.Name):
            assert node.id not in forbidden_names
            assert not node.id.endswith("DALImpl")
        elif isinstance(node, ast.Attribute):
            assert node.attr != "collection"
            assert node.attr != "cancelled_messages_collection"


def test_main_does_not_construct_legacy_sse_broker():
    tree = ast.parse(Path("main.py").read_text(), filename="main.py")
    forbidden_names = {"create_event_broker", "RedisBroker"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".", 1)[0] != "infrastructure"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".", 1)[0] != "infrastructure"
        elif isinstance(node, ast.Name):
            assert node.id not in forbidden_names
