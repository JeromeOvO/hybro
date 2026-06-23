import ast
import inspect
import tomllib
from pathlib import Path

from common.protocols import APIKeyRateLimiter, FileStorage, GatewayService, RateLimiter

FORBIDDEN_PLATFORM_IMPORT_PREFIXES = (
    "api",
    "models",
    "services",
    "modules",
    "database.mongodb",
    "config",
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
    from common.protocols import GatewayDiscoveryProvider
    from platform_module import (
        ObjectStoragePort,
        PlatformAgentAvatarManager,
        PlatformConfig,
        PlatformDeps,
        PlatformFacade,
        PlatformObjectStorage,
    )

    facade = PlatformFacade(config=PlatformConfig(), deps=PlatformDeps())

    assert isinstance(facade.gateway_service, GatewayService)
    assert isinstance(facade.discovery_service, GatewayDiscoveryProvider)
    assert isinstance(facade.gateway_rate_limiter, APIKeyRateLimiter)
    assert isinstance(facade.discovery_rate_limiter, APIKeyRateLimiter)
    assert isinstance(facade.agent_rate_limiter, RateLimiter)
    assert isinstance(facade.file_storage, FileStorage)
    assert facade.content_storage is not None
    assert "upload_file" in ObjectStoragePort.__dict__
    assert PlatformAgentAvatarManager is not None
    assert PlatformObjectStorage is not None


def test_platform_facade_properties_are_annotated_as_common_protocols():
    from typing import get_type_hints

    from common.protocols import GatewayDiscoveryProvider
    from platform_module import PlatformFacade

    discovery_hints = get_type_hints(PlatformFacade.discovery_service.fget)

    assert discovery_hints["return"] is GatewayDiscoveryProvider


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


def test_platform_discovery_returns_common_dto_without_any_escape_hatch():
    from typing import Any, get_type_hints

    from common.dto import GatewayDiscoveryResponse
    from platform_module.discovery import PlatformDiscovery

    hints = get_type_hints(PlatformDiscovery.discover_agents)

    assert hints["return"] is GatewayDiscoveryResponse
    assert hints["return"] is not Any


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

    assert not violations, (
        "Route rate limiter calls are outside the protocol:\n" + "\n".join(violations)
    )


def test_container_injects_gateway_and_discovery_rate_limiters_into_gateway_deps():
    source = Path("container.py").read_text()
    main_source = Path("main.py").read_text()
    assert "PlatformRouteAPIKeyRateLimiter" not in source
    assert "gateway.bind_gateway_dependencies(" not in source
    assert "discovery.bind_discovery_dependencies(" not in source
    assert "gateway.bind_gateway_dependencies(" not in main_source
    assert "discovery.bind_discovery_dependencies(" not in main_source
    assert "gateway_rate_limiter=platform_facade.gateway_rate_limiter" in source
    assert "discovery_rate_limiter=platform_facade.discovery_rate_limiter" in source


def test_container_uses_execution_room_message_center_runtime_for_startup_wiring():
    source = Path("container.py").read_text()
    main_source = Path("main.py").read_text()

    assert "room_center.room_message_center" not in source
    assert "execution_room_message_center.bind(" not in main_source
    assert "execution_room_message_center.bind(" in source
    assert "room_message_center=execution_room_message_center" in source


def test_container_imports_room_runtime_singleton_for_lifespan_bindings():
    tree = ast.parse(Path("container.py").read_text(), filename="container.py")
    main_tree = ast.parse(Path("main.py").read_text(), filename="main.py")
    room_runtime_uses = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "room_runtime"
    ]
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module != "app_shell.room_runtime":
            continue
        imported_names.update(alias.asname or alias.name for alias in node.names)

    main_room_runtime_imports = [
        node
        for node in ast.walk(main_tree)
        if isinstance(node, ast.ImportFrom) and node.module == "app_shell.room_runtime"
    ]

    assert room_runtime_uses
    assert "room_runtime" in imported_names
    assert main_room_runtime_imports == []


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
    from common.dto import AgentTaskResult
    from container import create_platform_deps, create_platform_facade
    from platform_module import PlatformConfig

    class AgentDeps:
        agent_registry = object()
        agent_matcher = object()
        agent_management = object()
        agent_call_counter = object()

    class Mongo:
        def collection(self, name: str):
            return {"collection": name}

    class AgentTransport:
        async def send_message(self, agent_url, message, **kwargs):
            return AgentTaskResult(
                task_id="task-1",
                agent_id=message.agent_id,
                status="completed",
            )

        async def stream_message(self, agent_url, message, **kwargs):
            if False:
                yield None

    class CardResolver:
        async def get_agent_card(self, agent_url):
            return None

    class ObjectStorage:
        async def put(self, key, body, content_type):
            return None

        async def get_presigned_url(self, key, ttl):
            return "https://files.example/file"

        async def delete(self, key):
            return None

    class ContentStorageRepository:
        async def get_by_hash(self, content_hash):
            return None

        async def upsert(self, record):
            return record

        async def get(self, reference_id):
            return None

        async def delete_expired(self, now):
            return 0

    deps = create_platform_deps(
        agent_deps=AgentDeps(),
        mongo=Mongo(),
        agent_transport=AgentTransport(),
        agent_card_resolver=CardResolver(),
        object_storage=ObjectStorage(),
        content_storage_repository=ContentStorageRepository(),
    )
    facade = create_platform_facade(config=PlatformConfig(), deps=deps)

    assert facade.deps.agent_registry is AgentDeps.agent_registry
    assert facade.deps.agent_call_counter is AgentDeps.agent_call_counter
    assert facade.deps.gateway_rate_limit_collection.collection_name == (
        "gateway_api_requests"
    )
    assert facade.deps.file_metadata_repository is not None
    assert isinstance(facade.gateway_service, GatewayService)


def test_container_platform_factory_uses_protocol_annotations():
    from typing import get_type_hints

    from common.protocols import (
        AgentCardResolver,
        AgentTransport,
        GatewayDiscoveryProvider,
    )
    from container import create_platform_deps
    from platform_module.deps import DiscoveryQueryExpander, LoggerLike

    hints = get_type_hints(create_platform_deps)

    assert hints["agent_transport"] is AgentTransport
    assert hints["agent_card_resolver"] == AgentCardResolver | None
    assert hints["discovery_provider"] == GatewayDiscoveryProvider | None
    assert hints["discovery_query_expander"] == DiscoveryQueryExpander | None
    assert hints["logger"] == LoggerLike | None


def test_platform_config_carries_discovery_confidence_threshold_from_settings():
    from container import create_platform_config

    class Settings:
        gateway_base_url = ""
        api_prefix = "/api/v1"
        gateway_rate_limit_per_key = 100
        gateway_rate_limit_global = 1000
        discovery_rate_limit_per_key = 100
        discovery_rate_limit_global = 1000
        discovery_default_limit = 5
        discovery_confidence_threshold = 0.73
        max_file_size_mb = 25
        s3_presigned_url_ttl = 3600
        compaction_content_ttl_days = 0

    config = create_platform_config(Settings())

    assert config.discovery_confidence_threshold == 0.73


def test_container_injects_discovery_query_expander_into_platform_deps():
    source = Path("container.py").read_text()
    main_source = Path("main.py").read_text()

    assert "discovery_query_expander=discovery_llm_service" in source
    assert "discovery_query_expander=discovery_llm_service" not in main_source


def test_container_passes_platform_object_storage_directly_to_runtime_consumers():
    source = Path("container.py").read_text()

    assert "PlatformObjectStorage" in source
    assert "platform_object_storage = PlatformObjectStorage(" in source
    assert "from app_shell.s3_service import s3_service" not in source
    assert "s3_service.bind_object_storage(" not in source
    assert "storage_service=platform_object_storage" in source
    avatar_manager_binding = (
        "PlatformAgentAvatarManager(\n                    platform_object_storage"
    )
    assert avatar_manager_binding in source
    assert "room_runtime.bind_object_storage(platform_object_storage)" in source
    assert "room_runtime.bind_s3_service(platform_object_storage)" not in source
    assert "s3_service=platform_object_storage" not in source
    assert "object_storage=platform_object_storage" in source
    assert source.index("platform_object_storage = PlatformObjectStorage(") < source.index(
        "storage_service=platform_object_storage"
    )
    assert "object_storage=object_storage" in source


def test_container_uses_platform_agent_avatar_manager_for_avatar_uploads():
    source = Path("container.py").read_text()

    assert "class AppShellAgentAvatarManager" not in source
    assert "PlatformAgentAvatarManager" in source
    assert "avatar_manager=PlatformAgentAvatarManager(" in source
    assert "agent_card.iconUrl" not in source


def test_container_constructs_object_storage_once_for_platform_wiring():
    source = Path("container.py").read_text()

    object_storage_pos = source.index("object_storage = create_object_storage_dal()")
    platform_storage_pos = source.index(
        "platform_object_storage = PlatformObjectStorage("
    )
    platform_deps_pos = source.index("platform_deps = create_platform_deps(")
    platform_deps_block = source[
        platform_deps_pos : source.index(
            "room_runtime.bind_attachment_metadata_reader",
            platform_deps_pos,
        )
    ]

    assert source.count("object_storage = create_object_storage_dal()") == 1
    assert source.count("platform_object_storage = PlatformObjectStorage(") == 1
    assert object_storage_pos < platform_storage_pos < platform_deps_pos
    platform_storage_binding = (
        "platform_object_storage = PlatformObjectStorage(\n                object_storage,"
    )
    assert platform_storage_binding in source
    assert "object_storage=object_storage" in platform_deps_block
    assert "object_storage=platform_object_storage" not in platform_deps_block


def test_direct_transport_does_not_partially_rebind_a2a_artifact_storage():
    path = Path("execution/dispatch/transports/direct.py")
    source = path.read_text()
    tree = ast.parse(source, filename=str(path))
    violations = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "bind_a2a_storage_dependencies"
    ]

    assert "from a2a_adapter import artifact_storage" not in source
    assert not violations, (
        "DirectTransport must use the startup-configured A2A artifact storage; "
        f"partial rebinds found at lines {violations}"
    )


def test_file_route_dependencies_are_read_from_gateway_deps_without_concrete_services():
    from types import SimpleNamespace

    from api_gateway.dependencies import get_file_storage, get_room_ownership_reader

    storage = object()
    room_ownership = object()
    deps = SimpleNamespace(
        file_storage=storage,
        room_ownership_reader=room_ownership,
    )

    assert get_file_storage(deps) is storage
    assert get_room_ownership_reader(deps) is room_ownership


def test_platform_config_is_scalar_only():
    from dataclasses import fields

    from platform_module import PlatformConfig

    config = PlatformConfig()
    scalar_types = (str, int, float, tuple)

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
                imports = [
                    (f"{node.module}.{alias.name}", node.module) for alias in node.names
                ]
            else:
                continue
            for imported_name, module in imports:
                if _is_forbidden(module):
                    violations.append(f"{path}:{node.lineno}: {imported_name}")

    assert not violations, "Forbidden platform imports:\n" + "\n".join(violations)


def test_container_injects_discovery_route_from_platform_facade():
    source = Path("container.py").read_text()
    main_source = Path("main.py").read_text()

    assert "discovery.bind_discovery_dependencies(" not in source
    assert "discovery.bind_discovery_dependencies(" not in main_source
    assert "discovery_service=platform_facade.discovery_service" in source


def test_gateway_discovery_is_not_backed_by_legacy_discovery_service():
    source = Path("main.py").read_text()

    assert "from app_shell.discovery_service import discovery_service" not in source
    assert "discovery_provider=discovery_service" not in source
