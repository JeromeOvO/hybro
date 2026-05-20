import ast
import importlib
import inspect
import json
from pathlib import Path

from fastapi import BackgroundTasks, Request
from fastapi.routing import APIRoute


FORBIDDEN_API_IMPORT_PREFIXES = (
    "database",
    "modules",
    "services",
    "delivery",
    "execution",
    "hub_runtime_bridge",
    "agent.repository",
    "room.repository",
    "context_memory.repository",
)

ALLOWLIST_PATH = Path("tests/fixtures/phase9_import_allowlist.json")


def _imported_module(node: ast.AST) -> str | None:
    if isinstance(node, ast.Import):
        return None
    if isinstance(node, ast.ImportFrom):
        return node.module
    return None


def _import_names(node: ast.AST) -> list[tuple[str, str]]:
    if isinstance(node, ast.Import):
        return [(alias.name, alias.name) for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module is not None:
        return [(f"{node.module}.{alias.name}", node.module) for alias in node.names]
    return []


def _is_forbidden(module: str) -> bool:
    return any(
        module == prefix or module.startswith(f"{prefix}.")
        for prefix in FORBIDDEN_API_IMPORT_PREFIXES
    )


def _load_allowlist() -> set[tuple[str, str]]:
    raw = json.loads(ALLOWLIST_PATH.read_text())
    allowed: set[tuple[str, str]] = set()
    for entry in raw:
        allowed.add((entry["path"], entry["import"]))
    return allowed


def _api_import_violations() -> list[str]:
    allowed = _load_allowlist()
    violations: list[str] = []
    for path in sorted(Path("api").glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Import | ast.ImportFrom):
                continue
            for imported_name, module in _import_names(node):
                if _is_forbidden(module) and (str(path), imported_name) not in allowed:
                    violations.append(f"{path}:{node.lineno}: {imported_name}")
    return violations


def test_api_modules_are_thin_route_adapters():
    violations = _api_import_violations()

    assert not violations, "Forbidden API imports:\n" + "\n".join(violations)


def test_phase9_route_inventory_is_recorded():
    routes = json.loads(Path("tests/fixtures/phase9_api_routes.json").read_text())

    assert routes
    for route in routes:
        assert {"path", "methods", "name", "auth_dependencies", "owning_protocol"}.issubset(
            route
        )


def test_phase9_route_inventory_matches_live_app_routes():
    from main import app

    docs_paths = {"/docs", "/docs/oauth2-redirect", "/openapi.json", "/redoc"}
    recorded_routes = json.loads(Path("tests/fixtures/phase9_api_routes.json").read_text())
    recorded = {
        (
            route["path"],
            tuple(route["methods"]),
            route["name"],
        ): route
        for route in recorded_routes
        if route["path"] not in docs_paths
    }
    live = {}
    for route in app.routes:
        if not isinstance(route, APIRoute) or route.path in docs_paths:
            continue
        methods = tuple(
            sorted(method for method in route.methods if method not in {"HEAD", "OPTIONS"})
        )
        response_model = (
            getattr(route.response_model, "__name__", str(route.response_model))
            if route.response_model is not None
            else None
        )
        dependencies = sorted(
            getattr(dependency.call, "__name__", repr(dependency.call))
            for dependency in route.dependant.dependencies
        )
        live[(route.path, methods, route.name)] = {
            "auth_dependencies": dependencies,
            "response_model": response_model,
        }

    assert set(recorded) == set(live)
    for key, route in recorded.items():
        assert route["response_model"] == live[key]["response_model"]
        assert sorted(route["auth_dependencies"]) == live[key]["auth_dependencies"]
        assert not route["owning_protocol"].startswith("blocked:")


def test_phase9_route_inventory_owners_resolve_to_real_symbols():
    routes = json.loads(Path("tests/fixtures/phase9_api_routes.json").read_text())
    symbolic_owners = {
        "fastapi.documentation",
        "legacy_workflow_decommission_manifest",
    }
    missing: list[str] = []

    for route in routes:
        owner = route["owning_protocol"]
        if owner in symbolic_owners or owner.startswith("blocked:"):
            continue
        module_name, _, symbol_name = owner.rpartition(".")
        if not module_name or not symbol_name:
            missing.append(f"{route['path']}: {owner}")
            continue
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            missing.append(f"{route['path']}: {owner} ({exc})")
            continue
        if not hasattr(module, symbol_name):
            missing.append(f"{route['path']}: {owner}")

    assert not missing, "Unresolved route owners:\n" + "\n".join(missing)


def test_legacy_workflow_routes_are_parameterless_410_adapters():
    from main import app

    recorded_routes = json.loads(Path("tests/fixtures/phase9_api_routes.json").read_text())
    legacy_keys = {
        (
            route["path"],
            tuple(route["methods"]),
            route["name"],
        )
        for route in recorded_routes
        if route["owning_protocol"] == "legacy_workflow_decommission_manifest"
    }

    violations: list[str] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        methods = tuple(
            sorted(method for method in route.methods if method not in {"HEAD", "OPTIONS"})
        )
        key = (route.path, methods, route.name)
        if key not in legacy_keys:
            continue
        query_params = [param.name for param in route.dependant.query_params]
        body_params = [param.name for param in route.dependant.body_params]
        dependencies = [
            getattr(dependency.call, "__name__", repr(dependency.call))
            for dependency in route.dependant.dependencies
        ]
        duplicate_dependencies = sorted(
            name for name in set(dependencies) if dependencies.count(name) > 1
        )
        if query_params or body_params or duplicate_dependencies:
            violations.append(
                f"{route.path}: query={query_params} body={body_params} "
                f"duplicate_deps={duplicate_dependencies}"
            )

    assert not violations, "Legacy 410 routes leak public params:\n" + "\n".join(
        violations
    )


def test_legacy_workflow_routes_advertise_410_in_openapi():
    from main import app

    openapi = app.openapi()
    routes = json.loads(Path("tests/fixtures/phase9_api_routes.json").read_text())
    legacy_routes = [
        route
        for route in routes
        if route["owning_protocol"] == "legacy_workflow_decommission_manifest"
    ]
    violations: list[str] = []

    for route in legacy_routes:
        for method in route["methods"]:
            responses = openapi["paths"][route["path"]][method.lower()]["responses"]
            if "410" not in responses or set(responses) == {"200"}:
                violations.append(f"{method} {route['path']}: {sorted(responses)}")

    assert not violations, "Legacy routes do not advertise 410:\n" + "\n".join(
        violations
    )


def test_legacy_workflow_routes_do_not_keep_runtime_injection_params():
    from main import app

    routes = json.loads(Path("tests/fixtures/phase9_api_routes.json").read_text())
    legacy_names = {
        route["name"]
        for route in routes
        if route["owning_protocol"] == "legacy_workflow_decommission_manifest"
    }
    forbidden_annotations = {Request, BackgroundTasks}
    violations: list[str] = []

    for route in app.routes:
        if not isinstance(route, APIRoute) or route.name not in legacy_names:
            continue
        for name, param in inspect.signature(route.endpoint).parameters.items():
            if name in route.path_format:
                continue
            if param.annotation in forbidden_annotations:
                violations.append(f"{route.path}: {name}")

    assert not violations, "Legacy 410 routes keep runtime params:\n" + "\n".join(
        violations
    )


def test_route_owner_protocols_match_handler_calls():
    from app_shell.bound import InspectionCenter, ViewSetRepository, WebhookTransport
    from app_shell.database_service import A2ATaskReader, AgentGroupStore
    from app_shell.health_check import HealthCheck

    expected_by_protocol = {
        InspectionCenter: {
            "inspect_a2a_connection",
            "inspect_agent_card",
        },
        AgentGroupStore: {
            "add_agent_group",
            "delete_agent_group",
            "get_agent_group_by_id",
            "get_agent_groups_by_owner",
            "update_agent_group",
        },
        A2ATaskReader: {
            "get_pending_task_messages_for_user",
            "get_room_agent_message_by_message_id",
            "get_task_messages_for_room",
        },
        ViewSetRepository: {
            "create",
            "delete",
            "get",
            "get_all",
            "patch",
            "update",
        },
        WebhookTransport: {"handle_webhook"},
        HealthCheck: {"check"},
    }

    missing: list[str] = []
    for protocol, expected_methods in expected_by_protocol.items():
        protocol_methods = {
            name
            for name, value in protocol.__dict__.items()
            if callable(value) and not name.startswith("_")
        }
        absent = sorted(expected_methods - protocol_methods)
        if absent:
            missing.append(f"{protocol.__name__}: {absent}")

    assert not missing, "Route owner protocol methods missing:\n" + "\n".join(
        missing
    )


def test_app_shell_protocol_surfaces_are_specific():
    from app_shell.bound import InspectionCenter, WebhookTransport

    for protocol in (InspectionCenter, WebhookTransport):
        for name, value in protocol.__dict__.items():
            if not callable(value) or name.startswith("_"):
                continue
            params = inspect.signature(value).parameters
            assert not any(
                parameter.kind in {
                    inspect.Parameter.VAR_POSITIONAL,
                    inspect.Parameter.VAR_KEYWORD,
                }
                for parameter in params.values()
            ), f"{protocol.__name__}.{name} uses wildcard parameters"
