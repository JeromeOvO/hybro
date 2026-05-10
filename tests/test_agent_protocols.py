import ast
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from common.protocols import (
    AgentManagement,
    AgentMatcher,
    AgentRegistry,
    AgentRegistryWriter,
    AgentRepository,
)


def _fake_facade():
    from agent import AgentFacade

    return AgentFacade(
        repository=AsyncMock(),
        vector=AsyncMock(),
        llm_provider=AsyncMock(),
        card_resolver=AsyncMock(),
        id_factory=lambda: "agent-id",
        now=lambda: datetime(2026, 5, 10, tzinfo=timezone.utc),
    )


def test_agent_facade_satisfies_runtime_protocols():
    facade = _fake_facade()

    assert isinstance(facade, AgentRegistry)
    assert isinstance(facade, AgentMatcher)
    assert isinstance(facade, AgentManagement)
    assert isinstance(facade, AgentRegistryWriter)


def test_agent_repository_satisfies_runtime_protocol():
    from agent.repository import AgentMongoRepository

    fake_mongo = MagicMock()
    fake_mongo.collection.return_value = AsyncMock()

    assert isinstance(AgentMongoRepository(mongo=fake_mongo), AgentRepository)


def test_agent_top_level_exports_are_explicit():
    import agent
    from agent import AgentFacade, AgentMongoRepository
    from agent.repository import AgentMongoRepository as RepositoryExport

    assert AgentFacade is agent.AgentFacade
    assert AgentMongoRepository is agent.AgentMongoRepository
    assert RepositoryExport is AgentMongoRepository
    assert list(agent.__all__) == ["AgentFacade", "AgentMongoRepository"]


def test_agent_packages_are_packaged():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    packages = set(pyproject["tool"]["setuptools"]["packages"])

    assert {"agent", "agent.repository"}.issubset(packages)


def test_agent_import_boundary():
    allowed_roots = set(sys.stdlib_module_names) | {
        "__future__",
        "common",
        "agent",
    }
    forbidden_roots = {
        "a2a_adapter",
        "api",
        "config",
        "container",
        "database",
        "infrastructure",
        "llm_gateway",
        "main",
        "models",
        "modules",
        "services",
    }

    _assert_import_boundary(Path("agent"), allowed_roots, forbidden_roots)


def test_agent_container_binds_single_facade_to_all_protocols():
    from common.protocols import (
        AgentManagement,
        AgentMatcher,
        AgentRegistry,
        AgentRegistryWriter,
    )
    from container import create_agent_deps

    fake_mongo = MagicMock()
    fake_mongo.collection.return_value = AsyncMock()

    deps = create_agent_deps(
        mongo=fake_mongo,
        vector=AsyncMock(),
        llm_provider=AsyncMock(),
        card_resolver=AsyncMock(),
    )

    assert isinstance(deps.agent_registry, AgentRegistry)
    assert isinstance(deps.agent_matcher, AgentMatcher)
    assert isinstance(deps.agent_management, AgentManagement)
    assert isinstance(deps.agent_registry_writer, AgentRegistryWriter)
    assert deps.agent_registry is deps.agent_matcher
    assert deps.agent_registry is deps.agent_management
    assert deps.agent_registry is deps.agent_registry_writer


def _assert_import_boundary(
    package_path: Path,
    allowed_roots: set[str],
    forbidden_roots: set[str],
) -> None:
    assert package_path.exists(), f"{package_path} does not exist"

    for path in package_path.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            imported_roots: set[str] = set()
            if isinstance(node, ast.Import):
                imported_roots = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level:
                continue
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots = {node.module.split(".")[0]}

            assert imported_roots.isdisjoint(forbidden_roots), (
                f"{path} imports forbidden root {imported_roots & forbidden_roots}"
            )
            unexpected = imported_roots - allowed_roots
            assert not unexpected, f"{path} imports unexpected root {unexpected}"
