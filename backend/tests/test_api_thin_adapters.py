import ast
import importlib
import inspect
import json
from dataclasses import fields, is_dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
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


def test_route_protocol_broad_shape_rules_cover_nested_any_and_bare_containers():
    from typing import Any, get_args, get_origin

    from common.dto.base import FrozenDTO
    from common.protocols import JsonValue

    class NestedBroadDTO(FrozenDTO):
        payload: dict[str, Any]

    def annotation_is_broad(annotation, seen: set[object] | None = None) -> bool:
        if seen is None:
            seen = set()
        if annotation in seen:
            return False
        seen.add(annotation)
        if annotation in {Any, object, inspect.Signature.empty}:
            return True
        if annotation in {dict, list, set, tuple}:
            return True
        origin = get_origin(annotation)
        if origin is None:
            if inspect.isclass(annotation) and issubclass(annotation, FrozenDTO):
                return any(
                    annotation_is_broad(field.annotation, seen)
                    for field in annotation.model_fields.values()
                )
            return False
        if origin in {dict, list, set, tuple} and not get_args(annotation):
            return True
        return any(annotation_is_broad(arg, seen) for arg in get_args(annotation))

    assert annotation_is_broad(dict)
    assert annotation_is_broad(list)
    assert annotation_is_broad(dict[str, Any])
    assert annotation_is_broad(list[dict[str, Any]])
    assert annotation_is_broad(NestedBroadDTO)
    assert not annotation_is_broad(dict[str, JsonValue])


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

    assert not violations, (
        "API route modules import other route modules:\n" + "\n".join(violations)
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
                        violations.append(
                            f"{path}:{node.lineno}: {node.name}.{arg.arg}"
                        )

    assert not violations, (
        "API bindings expose concrete dependency names:\n" + "\n".join(violations)
    )


def test_api_bindings_do_not_use_any_typed_dependency_seams():
    violations: list[str] = []
    paths = [
        *Path("api").glob("*.py"),
        *Path("api_gateway/routes").glob("*.py"),
        *Path("api_gateway/viewsets").glob("*.py"),
    ]

    for path in sorted(paths):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if _annotation_has_broad_shape(node.annotation):
                    violations.append(f"{path}:{node.lineno}: {node.target.id}")
            if isinstance(node, ast.FunctionDef) and node.name.startswith("bind_"):
                for arg in (*node.args.args, *node.args.kwonlyargs):
                    if _annotation_has_broad_shape(arg.annotation):
                        violations.append(
                            f"{path}:{node.lineno}: {node.name}.{arg.arg}"
                        )
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
                            default_index = index - (
                                len(arg_names) - len(node.args.defaults)
                            )
                            if default_index >= 0:
                                default = node.args.defaults[default_index]
                        if isinstance(default, ast.Call) and ast.unparse(
                            default.func
                        ).endswith("Depends"):
                            violations.append(
                                f"{path}:{node.lineno}: {node.name}.{arg.arg}"
                            )

    assert not violations, (
        "API bindings still use Any for dependency seams:\n" + "\n".join(violations)
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
    recorded_routes = json.loads(
        Path("tests/fixtures/phase9_api_routes.json").read_text()
    )
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
            sorted(
                method for method in route.methods if method not in {"HEAD", "OPTIONS"}
            )
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

    assert not violations, (
        "Route inventory auth_dependencies include non-auth dependencies:\n"
        + "\n".join(violations)
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
        if all(
            dependency.call is not get_current_user
            for dependency in route.dependant.dependencies
        ):
            methods = ",".join(sorted(mutation_methods.intersection(route.methods)))
            violations.append(f"{methods} {route.path} {route.name}")

    assert not violations, (
        "Agent ViewSet mutation routes lack Clerk auth:\n" + "\n".join(violations)
    )


def test_agent_viewset_read_routes_use_optional_user_visibility_dependency():
    from common.auth import get_optional_user
    from main import app

    violations = []
    for route in app.routes:
        if getattr(route, "path", "") not in {
            "/api/v1/agents",
            "/api/v1/agents/{item_id}",
        } or "GET" not in getattr(route, "methods", set()):
            continue
        dependency_calls = {dep.call for dep in route.dependant.dependencies}
        if get_optional_user not in dependency_calls:
            violations.append(route.path)

    assert not violations, (
        "Agent ViewSet read routes lack optional-user visibility dependency:\n"
        + "\n".join(violations)
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
                sorted(
                    method
                    for method in route.methods
                    if method not in {"HEAD", "OPTIONS"}
                )
            )
            violations.append(f"{methods} {route.path} {route.name}")

    assert not violations, "Routes duplicate Clerk auth dependency:\n" + "\n".join(
        violations
    )


