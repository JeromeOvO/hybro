import ast
import asyncio
import importlib
import inspect
import tomllib
from pathlib import Path

import pytest


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

    assert getattr(delivery, "__all__", []) == []

    from delivery.config import DeliveryConfig, DeliveryStartupPolicy
    from delivery.types import RoomSubscriptionLimitExceeded, TaskRunner

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
    assert hasattr(TaskRunner, "__call__")


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
