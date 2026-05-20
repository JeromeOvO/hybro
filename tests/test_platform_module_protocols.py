import ast
from pathlib import Path

import tomllib

from common.protocols import FileStorage, GatewayService, RateLimiter


FORBIDDEN_PLATFORM_IMPORT_PREFIXES = (
    "api",
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
    assert isinstance(facade.gateway_rate_limiter, RateLimiter)
    assert isinstance(facade.discovery_rate_limiter, RateLimiter)
    assert isinstance(facade.agent_rate_limiter, RateLimiter)
    assert isinstance(facade.file_storage, FileStorage)


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
