import ast
import importlib
import inspect
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import BackgroundTasks, Request
from fastapi.routing import APIRoute


FORBIDDEN_API_IMPORT_PREFIXES = (
    "database",
    "motor",
    "modules",
    "pymongo",
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
        if isinstance(node, ast.Name) and node.id == "object":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "Any":
            return True
        if isinstance(node, ast.Constant) and node.value is Ellipsis:
            return True
    return False


def _annotation_contains_broad_object(annotation) -> bool:
    if annotation is inspect.Signature.empty:
        return False
    if annotation is object:
        return True
    text = str(annotation)
    return (
        " object" in text
        or "[str, object]" in text
        or "| object" in text
        or "typing.Any" in text
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


def test_api_modules_do_not_import_other_route_modules_for_helpers():
    allowed_modules = {"api.agent_viewset", "api_gateway.viewsets.agent"}
    violations: list[str] = []
    for path in sorted(Path("api").glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.module is None:
                continue
            if (
                node.module.startswith("api.")
                and node.module not in {"api.viewset", "api_gateway.viewsets.base"}
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
        assert {
            "path",
            "methods",
            "name",
            "auth_dependencies",
            "dependencies",
            "owning_protocol",
            "status_code",
            "openapi_response_codes",
            "response_class",
        }.issubset(route)


def test_phase9_route_inventory_matches_live_app_routes():
    from main import app

    docs_paths = {"/docs", "/docs/oauth2-redirect", "/openapi.json", "/redoc"}
    recorded_routes = json.loads(Path("tests/fixtures/phase9_api_routes.json").read_text())
    openapi = app.openapi()
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
        response_class = getattr(route.response_class, "__name__", None)
        if response_class is None or response_class == "DefaultPlaceholder":
            response_class = None
        openapi_path = route.path_format
        method = next(iter(methods)).lower()
        openapi_responses = sorted(
            openapi["paths"][openapi_path][method].get("responses", {})
        )
        dependencies = sorted(
            getattr(dependency.call, "__name__", repr(dependency.call))
            for dependency in route.dependant.dependencies
        )
        auth_dependencies = [
            dependency
            for dependency in dependencies
            if dependency
            in {
                "get_api_key",
                "get_api_key_no_track",
                "get_current_user",
                "get_current_user_with_query_token",
                "get_optional_user",
            }
        ]
        live[(route.path, methods, route.name)] = {
            "module": getattr(route.endpoint, "__module__", ""),
            "dependencies": dependencies,
            "auth_dependencies": auth_dependencies,
            "openapi_response_codes": openapi_responses,
            "response_model": response_model,
            "response_class": response_class,
            "status_code": route.status_code,
        }

    assert set(recorded) == set(live)
    for key, route in recorded.items():
        assert route["module"] == live[key]["module"]
        assert route["response_model"] == live[key]["response_model"]
        assert sorted(route["dependencies"]) == live[key]["dependencies"]
        assert sorted(route["auth_dependencies"]) == live[key]["auth_dependencies"]
        assert route["status_code"] == live[key]["status_code"]
        assert route["openapi_response_codes"] == live[key]["openapi_response_codes"]
        assert route["response_class"] == live[key]["response_class"]
        assert not route["owning_protocol"].startswith("blocked:")


def test_route_inventory_auth_dependencies_are_only_auth_dependencies():
    auth_dependency_names = {
        "get_api_key",
        "get_api_key_no_track",
        "get_current_user",
        "get_current_user_with_query_token",
        "get_optional_user",
    }
    routes = json.loads(Path("tests/fixtures/phase9_api_routes.json").read_text())
    violations = [
        f"{route['path']} {route['name']}: {dependency}"
        for route in routes
        for dependency in route["auth_dependencies"]
        if dependency not in auth_dependency_names
    ]

    assert not violations, "Route inventory auth_dependencies include non-auth dependencies:\n" + "\n".join(
        violations
    )


def test_agent_viewset_mutations_require_clerk_auth():
    from common.auth import get_current_user
    from main import app

    mutation_methods = {"POST", "PUT", "PATCH", "DELETE"}
    violations: list[str] = []
    for route in app.routes:
        if (
            not isinstance(route, APIRoute)
            or route.path not in {"/api/v1/agents", "/api/v1/agents/{item_id}"}
            or not mutation_methods.intersection(route.methods)
        ):
            continue
        if all(dependency.call is not get_current_user for dependency in route.dependant.dependencies):
            methods = ",".join(sorted(mutation_methods.intersection(route.methods)))
            violations.append(f"{methods} {route.path} {route.name}")

    assert not violations, "Agent ViewSet mutation routes lack Clerk auth:\n" + "\n".join(
        violations
    )


def test_agent_viewset_read_routes_use_optional_user_visibility_dependency():
    from common.auth import get_optional_user
    from main import app

    violations = []
    for route in app.routes:
        if (
            getattr(route, "path", "") not in {"/api/v1/agents", "/api/v1/agents/{item_id}"}
            or "GET" not in getattr(route, "methods", set())
        ):
            continue
        dependency_calls = {dep.call for dep in route.dependant.dependencies}
        if get_optional_user not in dependency_calls:
            violations.append(route.path)

    assert not violations, "Agent ViewSet read routes lack optional-user visibility dependency:\n" + "\n".join(
        violations
    )


@pytest.mark.asyncio
async def test_agent_viewset_mutations_reject_non_owner(mock_user_2, sample_agent):
    from fastapi import HTTPException

    from api_gateway.viewsets import base as viewset
    from api_gateway.viewsets.agent import AgentViewSet

    repo = MagicMock()
    repo.pk_field = "agent_id"
    repo.get = AsyncMock(return_value=sample_agent.model_dump(mode="json"))
    repo.update = AsyncMock()

    with pytest.raises(HTTPException) as exc_info:
        await AgentViewSet()._handle_operation(
            viewset.UPDATE,
            repo,
            sample_agent.agent_id,
            sample_agent,
            user=mock_user_2,
        )

    assert exc_info.value.status_code == 403
    repo.update.assert_not_called()


def test_live_routes_do_not_duplicate_clerk_auth_dependency():
    from common.auth import get_current_user
    from main import app

    violations: list[str] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        auth_dependencies = [
            dependency.call
            for dependency in route.dependant.dependencies
            if dependency.call is get_current_user
        ]
        if len(auth_dependencies) > 1:
            methods = ",".join(
                sorted(method for method in route.methods if method not in {"HEAD", "OPTIONS"})
            )
            violations.append(f"{methods} {route.path} {route.name}")

    assert not violations, "Routes duplicate Clerk auth dependency:\n" + "\n".join(
        violations
    )


def test_streaming_routes_record_sse_media_type_and_headers():
    from main import app

    recorded_routes = json.loads(Path("tests/fixtures/phase9_api_routes.json").read_text())
    route_by_name = {route["name"]: route for route in recorded_routes}
    expected = {
        "gateway_stream": {
            "media_type": "text/event-stream",
            "headers": ["Cache-Control", "X-Accel-Buffering"],
        },
        "relay_events": {
            "media_type": "text/event-stream",
            "headers": ["Cache-Control", "Connection", "X-Accel-Buffering"],
        },
        "stream_room_messages": {
            "media_type": "text/event-stream",
            "headers": [
                "Cache-Control",
                "Connection",
                "Content-Type",
                "X-Accel-Buffering",
            ],
        },
    }

    for name, streaming in expected.items():
        assert route_by_name[name]["streaming_response"] == streaming
        route = next(
            route
            for route in app.routes
            if isinstance(route, APIRoute) and route.name == name
        )
        source = inspect.getsource(route.endpoint)
        assert "StreamingResponse" in source
        assert f'media_type="{streaming["media_type"]}"' in source
        for header in streaming["headers"]:
            assert f'"{header}"' in source


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
        if route["module"] == "api_gateway.routes.discovery_api_key_routes"
        and route["owning_protocol"] != "common.protocols.APIKeyStore"
    ]

    assert not violations, "API-key management routes must use APIKeyStore owner:\n" + "\n".join(
        violations
    )


def test_agent_viewset_mutations_record_vector_side_effect_protocols():
    routes = json.loads(Path("tests/fixtures/phase9_api_routes.json").read_text())
    expected = {
        "app_shell.bound.EmbeddingProvider",
        "app_shell.bound.VectorIndex",
    }
    violations: list[str] = []

    for route in routes:
        if route["path"] not in {"/api/v1/agents", "/api/v1/agents/{item_id}"}:
            continue
        if not set(route["methods"]) & {"POST", "PUT", "PATCH", "DELETE"}:
            continue
        supporting = set(route.get("supporting_protocols") or [])
        missing = expected - supporting
        if missing:
            violations.append(
                f"{route['path']} {','.join(route['methods'])}: missing {sorted(missing)}"
            )

    assert not violations, "Agent mutation route inventory omits side-effect protocols:\n" + "\n".join(
        violations
    )


def test_room_center_route_inventory_records_live_protocol_owners():
    routes = json.loads(Path("tests/fixtures/phase9_api_routes.json").read_text())
    by_name = {
        route["name"]: route
        for route in routes
        if route["module"] == "api_gateway.routes.room_routes"
    }
    expected = {
        "inquiry_active_runs": {
            "owner": "common.protocols.ExecutionEngine",
            "supporting": {"app_shell.database_service.A2ATaskReader"},
        },
        "send_message": {
            "owner": "common.protocols.ExecutionEngine",
            "supporting": {"app_shell.database_service.A2ATaskReader"},
        },
        "suggest_agents": {
            "owner": "app_shell.bound.AgentSelectionSuggester",
            "supporting": set(),
        },
    }
    violations: list[str] = []

    for name, expectation in expected.items():
        route = by_name[name]
        if route["owning_protocol"] != expectation["owner"]:
            violations.append(
                f"{name}: owner={route['owning_protocol']} expected={expectation['owner']}"
            )
        supporting = set(route.get("supporting_protocols") or [])
        missing = expectation["supporting"] - supporting
        if missing:
            violations.append(f"{name}: missing supporting {sorted(missing)}")

    assert not violations, "Room-center route inventory mismatches live protocols:\n" + "\n".join(
        violations
    )


def test_room_center_protocol_inventory_matches_handler_calls():
    from api import room_center

    expectations = {
        "inquiry_active_runs": (
            "common.protocols.ExecutionEngine",
            ["get_runs_for_room"],
        ),
        "send_message": (
            "common.protocols.ExecutionEngine",
            ["execute(", "start_orchestration"],
        ),
        "suggest_agents": (
            "app_shell.bound.AgentSelectionSuggester",
            ["suggest_agents"],
        ),
    }
    routes = json.loads(Path("tests/fixtures/phase9_api_routes.json").read_text())
    by_name = {
        route["name"]: route
        for route in routes
        if route["module"] == "api_gateway.routes.room_routes"
    }
    violations: list[str] = []

    for handler_name, (owner, method_names) in expectations.items():
        source = inspect.getsource(getattr(room_center, handler_name))
        if by_name[handler_name]["owning_protocol"] != owner:
            violations.append(f"{handler_name}: {by_name[handler_name]['owning_protocol']}")
        for call_marker in method_names:
            if call_marker not in source:
                violations.append(f"{handler_name}: missing call {call_marker}")

    assert not violations, "Room-center protocol inventory does not match handlers:\n" + "\n".join(
        violations
    )


def test_agent_viewset_routes_inject_repository_protocol_not_raw_database():
    from main import app

    violations: list[str] = []
    for route in app.routes:
        if (
            not isinstance(route, APIRoute)
            or route.path not in {"/api/v1/agents", "/api/v1/agents/{item_id}"}
        ):
            continue
        dependencies = sorted(
            getattr(dependency.call, "__name__", repr(dependency.call))
            for dependency in route.dependant.dependencies
        )
        if "get_viewset_db" in dependencies:
            violations.append(f"{route.path} {route.name}: raw db dependency")
        if "get_viewset_repository" not in dependencies:
            violations.append(
                f"{route.path} {route.name}: missing repository protocol dependency"
            )

    assert not violations, "Agent viewset routes bypass repository protocol:\n" + "\n".join(
        violations
    )


def test_viewset_route_adapter_does_not_manage_repository_construction_or_sessions():
    source = Path("api/viewset.py").read_text() + "\n" + Path(
        "api/agent_viewset.py"
    ).read_text()
    forbidden = (
        "Depends(get_viewset_db)",
        "db=Depends(",
        ".client.start_session",
    )
    violations = [value for value in forbidden if value in source]

    assert not violations, "ViewSet route adapters still manage datastore details:\n" + "\n".join(
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
        if route["module"] != "api_gateway.routes.orchestration_routes":
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
    from app_shell.bound import (
        AgentCapabilityIssueStore,
        AgentCenterRouteOwner,
        AgentLivenessChecker,
        AgentLookup,
        InspectionCenter,
        ViewSetRepository,
        WebhookTransport,
    )
    from app_shell.database_service import A2ATaskReader, AgentGroupStore
    from app_shell.health_check import HealthCheck
    from common.protocols import AgentAvatarManager, HubRelayManagement, HubStatusReader

    expected_by_protocol = {
        AgentAvatarManager: {
            "store_avatar",
        },
        AgentCapabilityIssueStore: {
            "get_issue_by_id",
            "get_issues_for_agent",
            "resolve_all_for_agent",
            "resolve_issue",
        },
        AgentCenterRouteOwner: {
            "_mask_sensitive_information",
            "get_agent_card_from_url",
            "get_agents_by_provider_id",
            "get_agents_with_conditions",
            "get_all_active_agents",
            "get_all_agents",
            "query_agent_by_agent_id",
            "register_agent",
            "remove_agent",
            "update_agent",
        },
        AgentLivenessChecker: {
            "__call__",
        },
        AgentLookup: {
            "get_agent_by_agent_id",
        },
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
        HubStatusReader: {
            "get_hub_status",
        },
        HubRelayManagement: {
            "connect_hub",
            "get_hub_status",
            "process_publish",
            "record_hub_heartbeat",
            "register_hub",
            "sync_agents",
        },
    }

    missing: list[str] = []
    for protocol, expected_methods in expected_by_protocol.items():
        protocol_methods = {
            name
            for name, value in protocol.__dict__.items()
            if callable(value)
            and (not name.startswith("_") or name in {"__call__", "_mask_sensitive_information"})
        }
        absent = sorted(expected_methods - protocol_methods)
        if absent:
            missing.append(f"{protocol.__name__}: {absent}")

    assert not missing, "Route owner protocol methods missing:\n" + "\n".join(
        missing
    )


def test_hub_route_dependencies_are_typed_with_route_facing_protocol():
    import inspect
    from typing import get_type_hints

    from api import hub
    from common.protocols import HubStatusReader

    bind_hints = get_type_hints(hub.bind_hub_dependencies)
    provider_hints = get_type_hints(hub.get_hub_relay_service)
    route_hints = get_type_hints(hub.hub_status_for_user)

    assert bind_hints["service"] is HubStatusReader
    assert provider_hints["return"] is HubStatusReader
    assert route_hints["svc"] is HubStatusReader
    assert "svc" in inspect.signature(hub.hub_status_for_user).parameters


def test_agent_routes_expose_typed_dependency_providers():
    import inspect
    from typing import get_type_hints

    from api import agent
    from app_shell.bound import (
        AgentCapabilityIssueStore,
        AgentCenterRouteOwner,
        AgentLivenessChecker,
        AgentLookup,
    )
    from common.protocols import AgentAvatarManager

    provider_expectations = {
        agent.get_agent_center: AgentCenterRouteOwner,
        agent.get_agent_service: AgentLookup,
        agent.get_capability_issue_service: AgentCapabilityIssueStore,
        agent.get_agent_avatar_manager: AgentAvatarManager,
        agent.get_agent_liveness_checker: AgentLivenessChecker,
    }
    for provider, expected_type in provider_expectations.items():
        assert get_type_hints(provider)["return"] is expected_type

    route_expectations = {
        agent.register_agent: {"center": AgentCenterRouteOwner},
        agent.get_agent_by_provider: {"center": AgentCenterRouteOwner},
        agent.delete_agent: {
            "center": AgentCenterRouteOwner,
            "agent_lookup": AgentLookup,
        },
        agent.update_agent: {
            "center": AgentCenterRouteOwner,
            "agent_lookup": AgentLookup,
        },
        agent.upload_agent_avatar: {
            "agent_lookup": AgentLookup,
            "avatar_manager": AgentAvatarManager,
        },
        agent.get_capability_issues: {
            "agent_lookup": AgentLookup,
            "issue_store": AgentCapabilityIssueStore,
        },
        agent.resolve_all_capability_issues: {
            "agent_lookup": AgentLookup,
            "issue_store": AgentCapabilityIssueStore,
        },
        agent.resolve_capability_issue: {
            "agent_lookup": AgentLookup,
            "issue_store": AgentCapabilityIssueStore,
        },
        agent.get_agent_card_from_url: {"center": AgentCenterRouteOwner},
        agent.get_agent: {
            "center": AgentCenterRouteOwner,
            "liveness_checker": AgentLivenessChecker,
        },
        agent.get_agent_list: {"center": AgentCenterRouteOwner},
        agent.get_all_active_agents: {"center": AgentCenterRouteOwner},
        agent.get_agent_list_with_conditions: {"center": AgentCenterRouteOwner},
    }
    missing: list[str] = []
    for handler, expected_params in route_expectations.items():
        hints = get_type_hints(handler)
        signature = inspect.signature(handler)
        for param_name, expected_type in expected_params.items():
            if param_name not in signature.parameters:
                missing.append(f"{handler.__name__}.{param_name}")
            elif hints.get(param_name) is not expected_type:
                missing.append(
                    f"{handler.__name__}.{param_name}: {hints.get(param_name)}"
                )

    assert not missing, "Agent routes hide route owner dependencies:\n" + "\n".join(
        missing
    )


def test_agent_dependency_providers_fail_when_unbound(monkeypatch):
    from api import agent

    monkeypatch.setattr(agent, "agent_center", None)
    monkeypatch.setattr(agent, "agent_service", None)
    monkeypatch.setattr(agent, "capability_issue_service", None)
    monkeypatch.setattr(agent, "agent_avatar_manager", None)
    monkeypatch.setattr(agent, "agent_liveness_checker", None)

    providers = (
        agent.get_agent_center,
        agent.get_agent_service,
        agent.get_capability_issue_service,
        agent.get_agent_avatar_manager,
        agent.get_agent_liveness_checker,
    )
    for provider in providers:
        with pytest.raises(RuntimeError):
            provider()


def test_agent_route_inventory_records_live_protocol_owners():
    routes = json.loads(Path("tests/fixtures/phase9_api_routes.json").read_text())
    by_name = {
        route["name"]: route
        for route in routes
        if route["module"] == "api_gateway.routes.agent_routes"
    }
    expectations = {
        "delete_agent": (
            "app_shell.bound.AgentCenterRouteOwner",
            {"app_shell.bound.AgentLookup"},
        ),
        "get_agent_by_provider": ("app_shell.bound.AgentCenterRouteOwner", set()),
        "get_agent": (
            "app_shell.bound.AgentCenterRouteOwner",
            {"app_shell.bound.AgentLivenessChecker"},
        ),
        "get_agent_card_from_url": ("app_shell.bound.AgentCenterRouteOwner", set()),
        "get_agent_list_with_conditions": (
            "app_shell.bound.AgentCenterRouteOwner",
            set(),
        ),
        "get_all_active_agents": ("app_shell.bound.AgentCenterRouteOwner", set()),
        "get_agent_list": ("app_shell.bound.AgentCenterRouteOwner", set()),
        "register_agent": ("app_shell.bound.AgentCenterRouteOwner", set()),
        "update_agent": (
            "app_shell.bound.AgentCenterRouteOwner",
            {"app_shell.bound.AgentLookup"},
        ),
        "upload_agent_avatar": (
            "common.protocols.AgentAvatarManager",
            {"app_shell.bound.AgentLookup"},
        ),
        "get_capability_issues": (
            "app_shell.bound.AgentCapabilityIssueStore",
            {"app_shell.bound.AgentLookup"},
        ),
        "resolve_all_capability_issues": (
            "app_shell.bound.AgentCapabilityIssueStore",
            {"app_shell.bound.AgentLookup"},
        ),
        "resolve_capability_issue": (
            "app_shell.bound.AgentCapabilityIssueStore",
            {"app_shell.bound.AgentLookup"},
        ),
    }
    violations: list[str] = []
    for name, (owner, supporting) in expectations.items():
        route = by_name[name]
        if route["owning_protocol"] != owner:
            violations.append(f"{name}: owner={route['owning_protocol']}")
        missing_supporting = supporting - set(route.get("supporting_protocols") or [])
        if missing_supporting:
            violations.append(f"{name}: missing {sorted(missing_supporting)}")

    assert not violations, "Agent route inventory mismatches live protocols:\n" + "\n".join(
        violations
    )


def test_sse_cancel_route_inventory_records_execution_owner():
    routes = json.loads(Path("tests/fixtures/phase9_api_routes.json").read_text())
    route = next(route for route in routes if route["name"] == "cancel_message")

    assert route["module"] == "api_gateway.routes.sse_routes"
    assert route["owning_protocol"] == "common.protocols.ExecutionEngine"
    assert set(route.get("supporting_protocols") or []) == {
        "app_shell.database_service.A2ATaskReader",
    }


def test_hitl_route_inventory_records_room_ownership_support():
    routes = json.loads(Path("tests/fixtures/phase9_api_routes.json").read_text())
    violations = [
        route["name"]
        for route in routes
            if route["module"] == "api_gateway.routes.hitl_routes"
        and "common.protocols.RoomOwnershipReader"
        not in set(route.get("supporting_protocols") or [])
    ]

    assert not violations, "HITL routes omit RoomOwnershipReader support:\n" + "\n".join(
        violations
    )


def test_file_upload_route_inventory_records_room_ownership_support():
    routes = json.loads(Path("tests/fixtures/phase9_api_routes.json").read_text())
    route = next(route for route in routes if route["name"] == "upload_file")

    assert route["module"] == "api_gateway.routes.files_routes"
    assert route["owning_protocol"] == "common.protocols.FileStorage"
    assert "common.protocols.RoomOwnershipReader" in set(
        route.get("supporting_protocols") or []
    )


def test_gateway_and_discovery_routes_record_rate_limit_support():
    routes = json.loads(Path("tests/fixtures/phase9_api_routes.json").read_text())
    violations = [
        f"{route['module']}.{route['name']}"
        for route in routes
        if route["module"] in {
            "api_gateway.routes.platform_gateway_routes",
            "api_gateway.routes.discovery_routes",
        }
        and "common.protocols.APIKeyRateLimiter"
        not in set(route.get("supporting_protocols") or [])
    ]

    assert not violations, "Gateway/discovery routes omit rate limiter support:\n" + "\n".join(
        violations
    )


def test_file_upload_route_uses_room_ownership_reader_protocol():
    import inspect
    from typing import get_type_hints

    from api import files
    from common.protocols import RoomOwnershipReader

    bind_hints = get_type_hints(files.bind_file_dependencies)
    provider_hints = get_type_hints(files.get_room_ownership_reader)
    route_hints = get_type_hints(files.upload_file)

    assert bind_hints["room_ownership"] is RoomOwnershipReader
    assert provider_hints["return"] is RoomOwnershipReader
    assert route_hints["room_ownership"] is RoomOwnershipReader
    assert "room_ownership" in inspect.signature(files.upload_file).parameters


def test_route_inventory_protocols_do_not_expose_broad_or_wildcard_shapes():
    from typing import Any

    symbolic_owners = {
        "fastapi.documentation",
        "legacy_workflow_decommission_manifest",
    }
    route_symbols = set()
    for route in json.loads(Path("tests/fixtures/phase9_api_routes.json").read_text()):
        route_symbols.add(route["owning_protocol"])
        route_symbols.update(route.get("supporting_protocols") or [])

    violations: list[str] = []
    for owner in sorted(route_symbols - symbolic_owners):
        module_name, _, symbol_name = owner.rpartition(".")
        protocol = getattr(importlib.import_module(module_name), symbol_name)
        for name, member in protocol.__dict__.items():
            if name.startswith("_") or not callable(member):
                continue
            signature = inspect.signature(member)
            if signature.return_annotation in {Any, object, inspect.Signature.empty}:
                violations.append(f"{owner}.{name}.return")
            for parameter in signature.parameters.values():
                if parameter.name == "self":
                    continue
                if parameter.kind in {
                    inspect.Parameter.VAR_POSITIONAL,
                    inspect.Parameter.VAR_KEYWORD,
                }:
                    violations.append(f"{owner}.{name}.{parameter.name}")
                elif parameter.annotation in {Any, object, inspect.Signature.empty}:
                    violations.append(f"{owner}.{name}.{parameter.name}")

    assert not violations, "Route inventory protocols expose broad shapes:\n" + "\n".join(
        violations
    )


def test_relay_route_dependencies_are_typed_with_route_facing_protocol():
    import inspect
    from typing import get_type_hints

    from api import relay
    from common.protocols import APIKeyPrincipal, HubRelayManagement

    bind_hints = get_type_hints(relay.bind_relay_dependencies)
    provider_hints = get_type_hints(relay.get_relay_service)
    route_hints = get_type_hints(relay.relay_register)
    register_sig = inspect.signature(HubRelayManagement.register_hub)

    assert bind_hints["service"] is HubRelayManagement
    assert provider_hints["return"] is HubRelayManagement
    assert route_hints["svc"] is HubRelayManagement
    assert register_sig.parameters["api_key"].annotation is APIKeyPrincipal


def test_relay_routes_are_owned_by_route_facing_protocol():
    routes = json.loads(Path("tests/fixtures/phase9_api_routes.json").read_text())
    violations = [
        f"{route['path']} {route['name']}: {route['owning_protocol']}"
        for route in routes
        if route["module"] == "api_gateway.routes.relay_routes"
        and route["owning_protocol"] != "common.protocols.HubRelayManagement"
    ]

    assert not violations, "Relay routes must use the route-facing HubRelayManagement protocol:\n" + "\n".join(
        violations
    )


def test_relay_route_protocol_does_not_expose_broad_or_wildcard_shapes():
    from typing import Any

    from common.protocols import HubRelayManagement

    violations: list[str] = []
    for name, member in HubRelayManagement.__dict__.items():
        if name.startswith("_") or not callable(member):
            continue
        signature = inspect.signature(member)
        if signature.return_annotation in {Any, object, inspect.Signature.empty}:
            violations.append(f"{name}.return")
        for parameter in signature.parameters.values():
            if parameter.name == "self":
                continue
            if parameter.kind in {
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            }:
                violations.append(f"{name}.{parameter.name}")
            elif parameter.annotation in {Any, object, inspect.Signature.empty}:
                violations.append(f"{name}.{parameter.name}")

    assert not violations, "Relay route protocol exposes broad shapes:\n" + "\n".join(
        violations
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
            if signature.return_annotation in {Any, object}:
                violations.append(f"{protocol.__name__}.{name} return")
            for parameter in signature.parameters.values():
                if parameter.annotation in {Any, object}:
                    violations.append(
                        f"{protocol.__name__}.{name}.{parameter.name}"
                    )

    assert not violations, "Route owner protocols expose broad annotations:\n" + "\n".join(
        violations
    )


def test_app_shell_route_protocols_do_not_expose_broad_annotations():
    import app_shell.bound as bound
    import app_shell.database_service as database_service
    import app_shell.health_check as health_check

    protocols = [
        getattr(bound, name)
        for name in bound.__all__
        if isinstance(getattr(bound, name, None), type)
    ]
    protocols.extend([database_service.A2ATaskReader, database_service.AgentGroupStore])
    protocols.append(health_check.HealthCheck)
    violations: list[str] = []

    for protocol in protocols:
        for name, value in protocol.__dict__.items():
            if not callable(value) or name.startswith("_"):
                continue
            signature = inspect.signature(value)
            if signature.return_annotation is inspect.Signature.empty:
                violations.append(f"{protocol.__name__}.{name} return")
            elif _annotation_contains_broad_object(signature.return_annotation):
                violations.append(f"{protocol.__name__}.{name} return")
            for parameter in signature.parameters.values():
                if parameter.name == "self":
                    continue
                if parameter.annotation is inspect.Signature.empty:
                    violations.append(
                        f"{protocol.__name__}.{name}.{parameter.name}"
                    )
                elif _annotation_contains_broad_object(parameter.annotation):
                    violations.append(
                        f"{protocol.__name__}.{name}.{parameter.name}"
                    )

    assert not violations, "App-shell route protocols expose broad shapes:\n" + "\n".join(
        violations
    )


def test_platform_route_protocols_do_not_expose_any_or_wildcard_params():
    from typing import Any

    from common.protocols import (
        APIKeyRateLimiter,
        FileStorage,
        GatewayDiscoveryProvider,
        GatewayService,
        RateLimiter,
    )

    protocols = (
        APIKeyRateLimiter,
        FileStorage,
        GatewayDiscoveryProvider,
        GatewayService,
        RateLimiter,
    )
    violations: list[str] = []

    for protocol in protocols:
        for name, value in protocol.__dict__.items():
            if not callable(value) or name.startswith("_"):
                continue
            signature = inspect.signature(value)
            if signature.return_annotation in {Any, inspect.Signature.empty}:
                violations.append(f"{protocol.__name__}.{name} return")
            if protocol in {GatewayDiscoveryProvider, GatewayService}:
                if _annotation_contains_broad_object(signature.return_annotation):
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
                if protocol in {GatewayDiscoveryProvider, GatewayService}:
                    if _annotation_contains_broad_object(parameter.annotation):
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
    from typing import get_type_hints

    from app_shell.bound import InspectionCenter
    from models.request import InspectionCenterRequest
    from models.response import (
        InsepectionCenterConnectionValidationResponse,
        InspectionCenterResponse,
    )

    inspect_card = get_type_hints(InspectionCenter.inspect_agent_card)
    inspect_connection = get_type_hints(InspectionCenter.inspect_a2a_connection)

    assert inspect_card["request"] is InspectionCenterRequest
    assert inspect_card["return"] is InspectionCenterResponse
    assert inspect_connection["request"] is InspectionCenterRequest
    assert inspect_connection["return"] is InsepectionCenterConnectionValidationResponse
