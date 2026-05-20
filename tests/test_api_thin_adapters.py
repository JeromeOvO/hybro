import ast
import importlib
import json
from pathlib import Path

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
