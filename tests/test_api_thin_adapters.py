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


def _annotation_has_broad_shape(annotation: ast.AST | None) -> bool:
    if annotation is None:
        return False
    for node in ast.walk(annotation):
        if isinstance(node, ast.Name) and node.id == "Any":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "Any":
            return True
        if isinstance(node, ast.Constant) and node.value is Ellipsis:
            return True
    return False


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


def test_api_modules_do_not_import_other_route_modules_for_helpers():
    allowed_modules = {"api.agent_viewset"}
    violations: list[str] = []
    for path in sorted(Path("api").glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.module is None:
                continue
            if (
                node.module.startswith("api.")
                and node.module != "api.viewset"
                and node.module not in allowed_modules
            ):
                violations.append(f"{path}:{node.lineno}: {node.module}")

    assert not violations, "API route modules import other route modules:\n" + "\n".join(
        violations
    )


def test_api_bindings_do_not_expose_concrete_store_or_service_names():
    forbidden_names = {
        "mongodb",
        "mongo",
        "s3_service",
        "storage_service",
        "openai_service",
    }
    violations: list[str] = []

    for path in sorted(Path("api").glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.target.id in forbidden_names:
                    violations.append(f"{path}:{node.lineno}: {node.target.id}")
            if isinstance(node, ast.FunctionDef) and node.name.startswith("bind_"):
                for arg in (*node.args.args, *node.args.kwonlyargs):
                    if arg.arg in forbidden_names:
                        violations.append(f"{path}:{node.lineno}: {node.name}.{arg.arg}")

    assert not violations, "API bindings expose concrete dependency names:\n" + "\n".join(
        violations
    )


def test_api_bindings_do_not_use_any_typed_dependency_seams():
    violations: list[str] = []

    for path in sorted(Path("api").glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if _annotation_has_broad_shape(node.annotation):
                    violations.append(f"{path}:{node.lineno}: {node.target.id}")
            if isinstance(node, ast.FunctionDef) and node.name.startswith("bind_"):
                for arg in (*node.args.args, *node.args.kwonlyargs):
                    if _annotation_has_broad_shape(arg.annotation):
                        violations.append(f"{path}:{node.lineno}: {node.name}.{arg.arg}")
            if isinstance(node, ast.FunctionDef) and node.name.startswith("get_"):
                if _annotation_has_broad_shape(node.returns):
                    violations.append(f"{path}:{node.lineno}: {node.name}.return")
            if isinstance(node, ast.AsyncFunctionDef):
                for arg in (*node.args.args, *node.args.kwonlyargs):
                    if _annotation_has_broad_shape(arg.annotation):
                        default = None
                        arg_names = [item.arg for item in node.args.args]
                        if arg.arg in arg_names:
                            index = arg_names.index(arg.arg)
                            default_index = index - (len(arg_names) - len(node.args.defaults))
                            if default_index >= 0:
                                default = node.args.defaults[default_index]
                        if isinstance(default, ast.Call) and ast.unparse(default.func).endswith("Depends"):
                            violations.append(f"{path}:{node.lineno}: {node.name}.{arg.arg}")

    assert not violations, "API bindings still use Any for dependency seams:\n" + "\n".join(
        violations
    )


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


def test_phase9_route_inventory_does_not_use_platform_implementation_owners():
    routes = json.loads(Path("tests/fixtures/phase9_api_routes.json").read_text())
    violations = [
        f"{route['path']}: {route['owning_protocol']}"
        for route in routes
        if route["owning_protocol"].startswith("platform_module.")
    ]

    assert not violations, "Routes must use common protocols, not platform implementations:\n" + "\n".join(
        violations
    )


def test_phase9_route_inventory_owners_are_protocol_symbols():
    routes = json.loads(Path("tests/fixtures/phase9_api_routes.json").read_text())
    symbolic_owners = {
        "fastapi.documentation",
        "legacy_workflow_decommission_manifest",
    }
    violations: list[str] = []

    for route in routes:
        owner = route["owning_protocol"]
        if owner in symbolic_owners or owner.startswith("blocked:"):
            continue
        module_name, _, symbol_name = owner.rpartition(".")
        symbol = getattr(importlib.import_module(module_name), symbol_name)
        if not getattr(symbol, "_is_protocol", False):
            violations.append(f"{route['path']}: {owner}")

    assert not violations, "Route owners must resolve to Protocols:\n" + "\n".join(
        violations
    )


def test_api_key_management_routes_are_owned_by_store_protocol():
    routes = json.loads(Path("tests/fixtures/phase9_api_routes.json").read_text())
    violations = [
        f"{route['path']} {route['name']}: {route['owning_protocol']}"
        for route in routes
        if route["module"] == "api.discovery_api_keys"
        and route["owning_protocol"] != "common.protocols.APIKeyStore"
    ]

    assert not violations, "API-key management routes must use APIKeyStore owner:\n" + "\n".join(
        violations
    )


def test_legacy_workflow_routes_keep_public_shape_without_execution_dependencies():
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
        expected_body = (
            ["req"]
            if route.path.startswith("/api/v1/orchestrationCenter/")
            and route.name != "process_room_user_message"
            else []
        )
        if query_params or body_params != expected_body or duplicate_dependencies:
            violations.append(
                f"{route.path}: query={query_params} body={body_params} "
                f"duplicate_deps={duplicate_dependencies}"
            )

    assert not violations, "Legacy 410 routes leak public params:\n" + "\n".join(
        violations
    )


def test_legacy_workflow_post_routes_keep_orchestration_request_body_schema():
    from main import app

    openapi = app.openapi()
    routes = json.loads(Path("tests/fixtures/phase9_api_routes.json").read_text())
    violations: list[str] = []
    for route in routes:
        if route["owning_protocol"] != "legacy_workflow_decommission_manifest":
            continue
        if route["module"] != "api.orchestration_center":
            continue
        if route["name"] == "process_room_user_message":
            continue
        operation = openapi["paths"][route["path"]]["post"]
        request_body = operation.get("requestBody", {})
        schema = (
            request_body.get("content", {})
            .get("application/json", {})
            .get("schema", {})
        )
        if "OrchestrationRequest" not in json.dumps(schema):
            violations.append(route["path"])

    assert not violations, "Legacy orchestration routes lost body schema:\n" + "\n".join(
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


def test_legacy_workflow_routes_keep_only_expected_runtime_injection_params():
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
            if route.name == "process_room_user_message" and name in {
                "request",
                "background_tasks",
            }:
                continue
            if name == "req":
                continue
            if param.annotation in forbidden_annotations:
                violations.append(f"{route.path}: {name}")

    assert not violations, "Legacy 410 routes keep unexpected runtime params:\n" + "\n".join(
        violations
    )


def test_legacy_410_routes_are_not_bound_to_legacy_execution_centers_at_startup():
    source = Path("main.py").read_text()
    forbidden = (
        "from modules.TaskCenter import TaskCenter",
        "from modules.WorkflowCenter import workflow_center",
        "task.bind_task_dependencies(",
        "orchestration_center.bind_orchestration_dependencies(",
    )
    violations = [value for value in forbidden if value in source]

    assert not violations, "Legacy 410 routes still bind execution centers:\n" + "\n".join(
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


def test_health_route_delegates_to_health_check_protocol():
    from main import app

    route = next(
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path == "/health"
    )
    source = inspect.getsource(route.endpoint)

    assert "get_health_check" in source
    assert ".check(" in source
    assert "services." not in source
    assert "settings." not in source


def test_health_check_service_uses_request_state_not_main_closures():
    import main
    from app_shell.health_check import AppShellHealthCheck

    main_source = inspect.getsource(main)
    health_source = inspect.getsource(AppShellHealthCheck)

    assert "_relay_streams_available" not in main_source
    assert "relay_streams_available=" not in main_source
    assert "request.app.state" in health_source
    assert "_relay_streams_available" not in health_source


def test_app_shell_protocol_surfaces_are_specific():
    from app_shell.bound import (
        InspectionCenter,
        ViewSetRepository,
        WebhookTransport,
        WebhookTransportFactory,
    )
    from app_shell.database_service import A2ATaskReader, AgentGroupStore

    for protocol in (
        InspectionCenter,
        ViewSetRepository,
        WebhookTransport,
        WebhookTransportFactory,
        A2ATaskReader,
        AgentGroupStore,
    ):
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


def test_route_owner_protocols_do_not_expose_any_annotations():
    from typing import Any

    from app_shell.bound import ViewSetRepository
    from app_shell.database_service import A2ATaskReader, AgentGroupStore
    from common.protocols import APIKeyStore

    protocols = (APIKeyStore, ViewSetRepository, A2ATaskReader, AgentGroupStore)
    violations: list[str] = []

    for protocol in protocols:
        for name, value in protocol.__dict__.items():
            if not callable(value) or name.startswith("_"):
                continue
            signature = inspect.signature(value)
            if signature.return_annotation is Any:
                violations.append(f"{protocol.__name__}.{name} return")
            for parameter in signature.parameters.values():
                if parameter.annotation is Any:
                    violations.append(
                        f"{protocol.__name__}.{name}.{parameter.name}"
                    )

    assert not violations, "Route owner protocols expose Any:\n" + "\n".join(
        violations
    )


def test_platform_route_protocols_do_not_expose_any_or_wildcard_params():
    from typing import Any

    from common.protocols import FileStorage, GatewayDiscoveryProvider, GatewayService

    protocols = (GatewayDiscoveryProvider, GatewayService, FileStorage)
    violations: list[str] = []

    for protocol in protocols:
        for name, value in protocol.__dict__.items():
            if not callable(value) or name.startswith("_"):
                continue
            signature = inspect.signature(value)
            if signature.return_annotation in {Any, inspect.Signature.empty}:
                violations.append(f"{protocol.__name__}.{name} return")
            for parameter in signature.parameters.values():
                if parameter.kind in {
                    inspect.Parameter.VAR_POSITIONAL,
                    inspect.Parameter.VAR_KEYWORD,
                }:
                    violations.append(f"{protocol.__name__}.{name}.{parameter.name}")
                if parameter.annotation is Any:
                    violations.append(
                        f"{protocol.__name__}.{name}.{parameter.name}"
                    )

    assert not violations, "Platform route protocols expose broad shapes:\n" + "\n".join(
        violations
    )


def test_app_shell_protocols_have_single_runtime_marker():
    for path in (Path("app_shell/bound.py"), Path("app_shell/database_service.py")):
        source = path.read_text()
        assert "@runtime_checkable\n@runtime_checkable" not in source


def test_inspection_protocol_uses_route_contract_types():
    from app_shell.bound import InspectionCenter
    from models.request import InspectionCenterRequest
    from models.response import (
        InsepectionCenterConnectionValidationResponse,
        InspectionCenterResponse,
    )

    inspect_card = inspect.signature(InspectionCenter.inspect_agent_card)
    inspect_connection = inspect.signature(InspectionCenter.inspect_a2a_connection)

    assert inspect_card.parameters["request"].annotation is InspectionCenterRequest
    assert inspect_card.return_annotation == InspectionCenterResponse
    assert (
        inspect_connection.parameters["request"].annotation
        is InspectionCenterRequest
    )
    assert (
        inspect_connection.return_annotation
        == InsepectionCenterConnectionValidationResponse
    )
