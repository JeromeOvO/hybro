from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HUB_PACKAGE = ROOT / "hub_runtime_bridge"
REMOVED_RUNTIME_PACKAGE = "app_" + "shell"


def _application_shell_module(name: str) -> str:
    return f"{REMOVED_RUNTIME_PACKAGE}.{name}"


def _py_files(base: Path) -> list[Path]:
    if not base.exists():
        return []
    return sorted(path for path in base.rglob("*.py") if path.is_file())


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(ROOT).with_suffix("").parts)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def _calls(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            parts = [func.attr]
            value = func.value
            while isinstance(value, ast.Attribute):
                parts.append(value.attr)
                value = value.value
            if isinstance(value, ast.Name):
                parts.append(value.id)
            names.add(".".join(reversed(parts)))
    return names


def test_hub_runtime_bridge_package_exists_and_is_registered() -> None:
    assert HUB_PACKAGE.is_dir()
    pyproject = (ROOT / "pyproject.toml").read_text()
    for package in [
        "hub_runtime_bridge",
        "hub_runtime_bridge.repository",
        "hub_runtime_bridge.service",
        "hub_runtime_bridge.transport",
        "hub_runtime_bridge.adapters",
        "hub_runtime_bridge.compat",
    ]:
        assert f'"{package}"' in pyproject


def test_hub_runtime_bridge_import_boundaries() -> None:
    forbidden_prefixes = (
        "execution",
        "modules",
        "api",
        "main",
        "container",
        "database.mongodb",
        "services",
        "agent",
        "room",
        "delivery",
        "dal",
        "config",
        "common.config",
    )
    legacy_model_allowlist = {
        "hub_runtime_bridge.adapters.legacy_models",
        "hub_runtime_bridge.adapters.api_key",
        "hub_runtime_bridge.adapters.legacy_failure",
        "hub_runtime_bridge.adapters.legacy_lifecycle",
        "hub_runtime_bridge.compat.relay_service",
    }
    a2a_agent_card_allowlist = {"hub_runtime_bridge.adapters.a2a_card"}
    a2a_task_status_allowlist = {"hub_runtime_bridge.adapters.legacy_failure"}

    for path in _py_files(HUB_PACKAGE):
        module = _module_name(path)
        imported = _imports(path)

        for name in imported:
            assert not any(
                name == prefix or name.startswith(prefix + ".")
                for prefix in forbidden_prefixes
            ), f"{module} imports forbidden boundary {name}"

            if name == "models" or name.startswith("models."):
                assert module in legacy_model_allowlist

            if name == "a2a.types" and "AgentCard" in path.read_text():
                assert module in a2a_agent_card_allowlist
            if name == "a2a.types" and "TaskStatus" in path.read_text():
                assert module in a2a_task_status_allowlist


def test_hub_runtime_bridge_no_forbidden_direct_writes_or_tasks() -> None:
    forbidden_text = [
        "agents_collection",
        "upsert_hub_agent(",
        "enable_task_tracking_on_message(",
        "SSETransport.is_cancelled",
        "asyncio.create_task(",
    ]
    legacy_failure = HUB_PACKAGE / "adapters" / "legacy_failure.py"
    for path in _py_files(HUB_PACKAGE):
        text = path.read_text()
        for needle in forbidden_text:
            if needle == "enable_task_tracking_on_message(" and path == legacy_failure:
                continue
            assert needle not in text, f"{path.relative_to(ROOT)} contains {needle}"


def test_execution_depends_on_common_not_relay_or_hub_concrete() -> None:
    allowlist = json.loads(
        (ROOT / "tests/fixtures/phase8_hub_import_allowlist.json").read_text()
    )
    temporary = set(allowlist.get("temporary_imports", []))
    for path in _py_files(ROOT / "execution"):
        module = _module_name(path)
        if module in temporary:
            continue
        imported = _imports(path)
        assert _application_shell_module("relay_service") not in imported
        assert not any(name.startswith("hub_runtime_bridge") for name in imported)


def test_execution_relay_transport_is_outbound_only() -> None:
    path = ROOT / "execution/dispatch/transports/relay.py"
    text = path.read_text()
    imported = _imports(path)

    assert "handle_publish_event" not in text
    assert "models.hub" not in imported
    assert "database.mongodb" not in imported
    assert _application_shell_module("relay_service") not in imported


def test_relay_service_runtime_is_owned_by_hub_runtime_bridge() -> None:
    relay = ROOT / "hub_runtime_bridge/compat/relay_service.py"
    relay_imports = _imports(relay)
    relay_calls = _calls(relay)
    relay_text = relay.read_text()
    deleted_modules = {
        name: _application_shell_module(name)
        for name in ("relay_service", "redis_runtime", "delivery_runtime")
    }

    assert "hub_runtime_bridge.facade" in relay_imports
    assert deleted_modules["relay_service"] not in relay_imports
    assert deleted_modules["relay_service"] not in relay_text
    assert deleted_modules["redis_runtime"] not in relay_imports
    assert deleted_modules["redis_runtime"] not in relay_text
    assert deleted_modules["delivery_runtime"] not in relay_imports
    assert deleted_modules["delivery_runtime"] not in relay_text
    assert "sse_manager" not in relay_text
    assert ("App" + "Shell" + "RelayStreamService") not in relay_text
    assert not any(name.startswith("modules") for name in relay_imports)
    assert "execution.facade" not in relay_text
    assert "AgentResponseHandler" not in relay_text
    assert "RelayTransport" not in relay_text
    assert "AgentResponseHandler" not in relay_calls
    assert "RelayTransport" not in relay_calls


def test_legacy_relay_does_not_import_delivery_runtime_concrete() -> None:
    relay = ROOT / "hub_runtime_bridge/compat/relay_service.py"
    relay_imports = _imports(relay)
    relay_text = relay.read_text()
    delivery_runtime_module = _application_shell_module("delivery_runtime")

    assert delivery_runtime_module not in relay_imports
    assert "SSEManager" not in relay_text
    assert "self._sse" not in relay_text


def test_legacy_relay_does_not_import_redis_runtime_concretes() -> None:
    relay = ROOT / "hub_runtime_bridge/compat/relay_service.py"
    relay_imports = _imports(relay)
    relay_text = relay.read_text()
    redis_runtime_module = _application_shell_module("redis_runtime")

    assert redis_runtime_module not in relay_imports
    assert ("App" + "Shell" + "RelayStreamService") not in relay_text
    assert ("App" + "Shell" + "LeaderElection") not in relay_text


def test_legacy_relay_has_single_transport_state() -> None:
    relay = ROOT / "hub_runtime_bridge/compat/relay_service.py"
    tree = ast.parse(relay.read_text(), filename=str(relay))

    private_transport_refs = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "_relay_transport"
    ]
    assert private_transport_refs == []


def test_application_shell_routes_internal_hub_events_through_hub_router() -> None:
    main = (ROOT / "main.py").read_text()
    container = (ROOT / "container.py").read_text()

    assert "class HubDeps" in container
    assert "def create_hub_facade" in container
    assert "def create_hub_deps" in container
    assert (
        "_execution_deps.hub_agent_response_sink.handle_hub_agent_response" not in main
    )
    assert "internal_response_dispatcher" in container
    assert "router.dispatch_hub_internal_response" in container


def test_container_uses_owner_runtime_modules_for_delivery_redis_and_relay() -> None:
    container_path = ROOT / "container.py"
    container = container_path.read_text()
    container_imports = _imports(container_path)
    deleted_modules = {
        name: _application_shell_module(name)
        for name in ("delivery_runtime", "redis_runtime", "room_lock")
    }

    assert deleted_modules["delivery_runtime"] not in container_imports
    assert deleted_modules["redis_runtime"] not in container_imports
    assert deleted_modules["room_lock"] not in container_imports
    assert ("create_" + "app_" + "shell" + "_redis_runtime") not in container
    assert ("App" + "Shell" + "RelayHubStore") not in container
    assert "RedisRoomDistributedLock" not in container
    assert "class RedisRuntimeDeps" in container
    assert "def create_redis_runtime_deps" in container
    assert "RedisKVImpl" in container
    assert "RedisStreamsImpl" in container
    assert "LeaderElectorImpl" in container
    assert "RoomRedisDistributedLock" in container
    assert "RelayStreamService" in container


def test_relay_and_hub_route_inventory_matches_fixture() -> None:
    from api.hub import router as hub_router
    from api.relay import router as relay_router

    actual = [
        {
            "methods": sorted(method for method in route.methods if method != "HEAD"),
            "path": route.path,
        }
        for route in [*relay_router.routes, *hub_router.routes]
    ]
    expected = json.loads((ROOT / "tests/fixtures/phase8_hub_routes.json").read_text())

    assert sorted(actual, key=lambda item: (item["path"], item["methods"])) == sorted(
        expected, key=lambda item: (item["path"], item["methods"])
    )


def test_phase8_hub_runtime_bridge_design_reflected_in_code() -> None:
    """Gate: journal sidecar and owned publish not via Delivery."""
    journal_path = ROOT / "hub_runtime_bridge" / "hub_response_journal.py"
    assert journal_path.is_file()
    assert "hub_response_journal" in journal_path.read_text(encoding="utf-8")

    hub_publish_path = ROOT / "hub_runtime_bridge/service/hub_publish.py"
    hub_publish_text = hub_publish_path.read_text(encoding="utf-8")
    hub_publish_imports = _imports(hub_publish_path)
    assert "_journal" in hub_publish_text
    assert "dispatch_hub_internal_response" in hub_publish_text
    assert not any(name.startswith("delivery") for name in hub_publish_imports)
