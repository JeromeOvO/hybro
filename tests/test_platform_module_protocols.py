import ast
import inspect
from pathlib import Path

import tomllib

from common.protocols import APIKeyRateLimiter, FileStorage, GatewayService, RateLimiter


FORBIDDEN_PLATFORM_IMPORT_PREFIXES = (
    "api",
    "models",
    "services",
    "modules",
    "database.mongodb",
    "config.settings",
)


def _is_forbidden(module: str) -> bool:
    return any(
        module == prefix or module.startswith(f"{prefix}.")
        for prefix in FORBIDDEN_PLATFORM_IMPORT_PREFIXES
    )


def test_platform_module_packages_are_registered():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    packages = set(pyproject["tool"]["setuptools"]["packages"])

    assert {"platform_module", "platform_module.adapters"}.issubset(packages)


def test_platform_facade_exposes_common_protocol_surfaces():
    from platform_module import PlatformConfig, PlatformDeps, PlatformFacade

    facade = PlatformFacade(config=PlatformConfig(), deps=PlatformDeps())

    assert isinstance(facade.gateway_service, GatewayService)
    assert facade.discovery_service is not None
    assert isinstance(facade.gateway_rate_limiter, APIKeyRateLimiter)
    assert isinstance(facade.discovery_rate_limiter, APIKeyRateLimiter)
    assert isinstance(facade.agent_rate_limiter, RateLimiter)
    assert isinstance(facade.file_storage, FileStorage)
    assert facade.content_storage is not None


def test_gateway_protocol_matches_route_facing_platform_surface():
    from common.protocols.platform_protocols import GatewayService
    from platform_module.gateway import PlatformGateway

    for method_name in (
        "discover_agents",
        "get_agent_card",
        "send_message",
        "prepare_stream",
        "stream_message",
    ):
        protocol_params = list(
            inspect.signature(getattr(GatewayService, method_name)).parameters
        )
        implementation_params = list(
            inspect.signature(getattr(PlatformGateway, method_name)).parameters
        )
        assert implementation_params == protocol_params


def test_api_routes_call_only_api_key_rate_limiter_protocol_methods():
    from common.protocols.platform_protocols import APIKeyRateLimiter

    protocol_methods = {
        name
        for name, value in APIKeyRateLimiter.__dict__.items()
        if callable(value) and not name.startswith("_")
    }
    violations: list[str] = []

    for path in (Path("api/gateway.py"), Path("api/discovery.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            if not isinstance(node.value, ast.Name):
                continue
            if node.value.id == "rate_limiter" and node.attr not in protocol_methods:
                violations.append(f"{path}:{node.lineno}: rate_limiter.{node.attr}")

    assert not violations, "Route rate limiter calls are outside the protocol:\n" + "\n".join(
        violations
    )


def test_main_binds_gateway_and_discovery_rate_limiters_from_platform_facade():
    source = Path("main.py").read_text()

    assert "PlatformRouteAPIKeyRateLimiter" not in source
    assert "gateway.bind_gateway_dependencies(\n                platform_facade.gateway_service,\n                platform_facade.gateway_rate_limiter" in source
    assert "discovery.bind_discovery_dependencies(\n                platform_facade.discovery_service,\n                platform_facade.discovery_rate_limiter" in source


def test_container_builds_platform_config_from_scalar_settings():
    from container import create_platform_config

    class Settings:
        gateway_base_url = "https://api.example/v1"
        api_prefix = "/custom-api"
        gateway_rate_limit_per_key = 7
        gateway_rate_limit_global = 70
        discovery_rate_limit_per_key = 8
        discovery_rate_limit_global = 80
        max_file_size_mb = 3
        s3_presigned_url_ttl = 90
        compaction_content_ttl_days = 2

    config = create_platform_config(Settings())

    assert config.gateway_base_url == "https://api.example/v1"
    assert config.api_prefix == "/custom-api"
    assert config.gateway_rate_limit_per_key == 7
    assert config.discovery_rate_limit_global == 80
    assert config.max_upload_size_bytes == 3 * 1024 * 1024
    assert config.presigned_url_ttl_seconds == 90
    assert config.content_storage_ttl_seconds == 2 * 24 * 60 * 60
    assert "image/png" in config.allowed_mime_types


def test_container_preserves_disabled_platform_rate_limits():
    from container import create_platform_config

    class Settings:
        gateway_base_url = ""
        api_prefix = "/api/v9"
        gateway_rate_limit_per_key = None
        gateway_rate_limit_global = None
        discovery_rate_limit_per_key = None
        discovery_rate_limit_global = None
        max_file_size_mb = 25
        s3_presigned_url_ttl = 3600
        compaction_content_ttl_days = 0

    config = create_platform_config(Settings())

    assert config.gateway_rate_limit_per_key is None
    assert config.gateway_rate_limit_global is None
    assert config.discovery_rate_limit_per_key is None
    assert config.discovery_rate_limit_global is None


def test_container_builds_platform_facade_from_protocol_dependencies():
    from container import create_platform_deps, create_platform_facade
    from platform_module import PlatformConfig

    class AgentDeps:
        agent_registry = object()
        agent_matcher = object()
        agent_management = object()

    class Mongo:
        def collection(self, name: str):
            return {"collection": name}

    deps = create_platform_deps(
        agent_deps=AgentDeps(),
        mongo=Mongo(),
        agent_transport=object(),
        agent_card_resolver=object(),
        object_storage=object(),
        content_storage_repository=object(),
    )
    facade = create_platform_facade(config=PlatformConfig(), deps=deps)

    assert facade.deps.agent_registry is AgentDeps.agent_registry
    assert facade.deps.gateway_rate_limit_collection.collection_name == (
        "gateway_api_requests"
    )
    assert facade.deps.file_metadata_repository is not None
    assert isinstance(facade.gateway_service, GatewayService)


def test_file_route_dependencies_can_be_rebound_without_concrete_services():
    from api.files import (
        bind_file_dependencies,
        get_file_storage,
        get_room_ownership_verifier,
    )

    storage = object()

    async def verifier(room_id, user):
        return None

    bind_file_dependencies(storage, verifier)

    assert get_file_storage() is storage
    assert get_room_ownership_verifier() is verifier


def test_platform_config_is_scalar_only():
    from dataclasses import fields

    from platform_module import PlatformConfig

    config = PlatformConfig()
    scalar_types = (str, int, tuple)

    assert config.max_upload_size_bytes > 0
    for field in fields(config):
        assert isinstance(getattr(config, field.name), scalar_types)


def test_platform_module_does_not_import_app_shell_or_legacy_services():
    violations: list[str] = []
    for path in sorted(Path("platform_module").rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports = [(alias.name, alias.name) for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imports = [(f"{node.module}.{alias.name}", node.module) for alias in node.names]
            else:
                continue
            for imported_name, module in imports:
                if _is_forbidden(module):
                    violations.append(f"{path}:{node.lineno}: {imported_name}")

    assert not violations, "Forbidden platform imports:\n" + "\n".join(violations)


def test_main_binds_discovery_route_to_platform_facade():
    source = Path("main.py").read_text()

    assert "discovery.bind_discovery_dependencies(\n                platform_facade.discovery_service" in source


def test_gateway_discovery_is_not_backed_by_legacy_discovery_service():
    source = Path("main.py").read_text()

    assert "from services.discovery_service import discovery_service" not in source
    assert "discovery_provider=discovery_service" not in source
