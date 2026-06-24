import ast
import tomllib
from pathlib import Path

FORBIDDEN_API_GATEWAY_IMPORTS = (
    "database.mongodb",
    "modules",
    "app_shell.bound",
    "app_shell.gateway_service",
    "app_shell.file_upload_service",
    "app_shell.rate_limit_service",
)
MODULE_ROUTE_PROTOCOL_IMPORTS = {
    "agent.protocols",
    "room.protocols",
    "context_memory.protocols",
}
FORBIDDEN_ROUTE_MODULE_ROOTS = {"agent", "room", "context_memory", "a2a_adapter"}


def _api_gateway_py_files():
    root = Path("api_gateway")
    if not root.exists():
        return []
    return sorted(root.rglob("*.py"))


def test_api_gateway_package_exists():
    assert Path("api_gateway").is_dir()
    assert Path("api_gateway/routes").is_dir()


def test_api_gateway_does_not_import_forbidden_concrete_modules():
    violations = []
    for path in _api_gateway_py_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.ImportFrom):
                module = node.module
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name
                    if any(
                        module == forbidden or module.startswith(f"{forbidden}.")
                        for forbidden in FORBIDDEN_API_GATEWAY_IMPORTS
                    ):
                        violations.append(f"{path}: import {module}")
                continue

            if module and any(
                module == forbidden or module.startswith(f"{forbidden}.")
                for forbidden in FORBIDDEN_API_GATEWAY_IMPORTS
            ):
                violations.append(f"{path}: from {module} import ...")

    assert violations == []


def test_gateway_routes_import_only_module_protocol_surfaces():
    violations = []
    paths = [
        *Path("api_gateway/routes").glob("*.py"),
        *Path("api_gateway/viewsets").glob("*.py"),
    ]

    for path in sorted(paths):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
            elif isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)

            for module in modules:
                root = module.split(".", 1)[0]
                if root not in FORBIDDEN_ROUTE_MODULE_ROOTS:
                    continue
                if module in MODULE_ROUTE_PROTOCOL_IMPORTS:
                    continue
                violations.append(f"{path}: import {module}")

    assert violations == []


def test_api_gateway_route_files_do_not_use_legacy_prefix():
    route_dir = Path("api_gateway/routes")
    route_files = route_dir.glob("*.py") if route_dir.exists() else []

    assert [path.name for path in route_files if path.name.startswith("legacy_")] == []


def test_main_mounts_only_gateway_router_for_api_prefix():
    tree = ast.parse(Path("main.py").read_text(), filename="main.py")
    include_calls = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "include_router"
            and isinstance(func.value, ast.Name)
            and func.value.id == "app"
        ):
            include_calls.append(node)

    assert len(include_calls) >= 1
    call = include_calls[0]
    assert isinstance(call.args[0], ast.Attribute)
    assert isinstance(call.args[0].value, ast.Name)
    assert call.args[0].value.id == "api_gateway"
    assert call.args[0].attr == "router"


def test_main_does_not_import_old_api_route_modules():
    tree = ast.parse(Path("main.py").read_text(), filename="main.py")
    violations = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "api" or node.module.startswith("api."):
                violations.append(f"from {node.module} import ...")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "api" or alias.name.startswith("api."):
                    violations.append(f"import {alias.name}")

    assert violations == []


def test_old_api_route_modules_are_compatibility_shims_only():
    route_modules = {
        "a2a_tasks.py",
        "agent.py",
        "agent_viewset.py",
        "agent_group.py",
        "discovery.py",
        "discovery_api_keys.py",
        "files.py",
        "gateway.py",
        "hitl.py",
        "hub.py",
        "inspection_center.py",
        "memory_center.py",
        "relay.py",
        "room_center.py",
        "sse.py",
        "viewset.py",
        "webhooks.py",
    }

    violations = []
    for filename in sorted(route_modules):
        path = Path("api") / filename
        source = path.read_text()
        if "Compatibility shim" not in source:
            violations.append(f"{path}: missing compatibility shim marker")
        if "APIRouter(" in source or "@router." in source:
            violations.append(f"{path}: still declares routes")

    assert violations == []


def test_room_route_owner_protocol_covers_room_route_calls():
    import room.protocols as protocols

    required_methods = {
        "create_new_room",
        "inquiry_rooms_by_room_owner_id",
        "inquiry_room_messages_by_room_id",
        "inquiry_room_setting",
        "inquiry_active_runs",
        "update_room_agent_set",
        "update_room_name",
        "update_room_extend_info",
    }

    assert required_methods.issubset(set(protocols.RoomCenterCompatibility.__dict__))


def test_api_gateway_packages_are_registered_for_distribution():
    setuptools_config = tomllib.loads(Path("pyproject.toml").read_text())["tool"][
        "setuptools"
    ]
    packages = set(setuptools_config["packages"])

    assert {
        "api",
        "api_gateway",
        "api_gateway.routes",
        "api_gateway.viewsets",
        "common.client",
        "common.middleware",
        "common.server",
        "common.utils",
    }.issubset(packages)
    assert "main" in set(setuptools_config.get("py-modules", []))


def test_a2a_sdk_dependency_is_pinned_to_compatible_major_version():
    from packaging.requirements import Requirement

    project_config = tomllib.loads(Path("pyproject.toml").read_text())["project"]
    dependencies = {
        Requirement(dependency).name: Requirement(dependency)
        for dependency in project_config["dependencies"]
    }

    assert "a2a-sdk" in dependencies
    specifier = dependencies["a2a-sdk"].specifier
    assert specifier.contains("0.3.25")
    assert not specifier.contains("1.0.3")
