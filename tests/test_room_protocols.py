import ast
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from common.protocols import (
    MessageRepository,
    RoomHistoryReader,
    RoomManagement,
    RoomMessageStore,
    RoomOwnershipReader,
    RoomRegistry,
    RoomRepository,
)


def _fake_facade():
    from common.dto import AgentInfo
    from room import RoomFacade

    membership_source = AsyncMock()
    membership_source.get_saved_group.return_value = None
    membership_source.list_current_agents.return_value = []

    agent_registry = AsyncMock()
    agent_registry.get_agents_by_ids.return_value = [
        AgentInfo(agent_id="agent-1", name="Agent One")
    ]

    return RoomFacade(
        repository=AsyncMock(),
        message_repository=AsyncMock(),
        agent_registry=agent_registry,
        membership_source=membership_source,
        id_factory=lambda: "room-id",
        now=lambda: datetime(2026, 5, 11, tzinfo=timezone.utc),
    )


def test_room_facade_satisfies_runtime_protocols():
    facade = _fake_facade()

    assert isinstance(facade, RoomRegistry)
    assert isinstance(facade, RoomManagement)
    assert isinstance(facade, RoomMessageStore)
    assert isinstance(facade, RoomHistoryReader)
    assert isinstance(facade, RoomOwnershipReader)


def test_room_repositories_satisfy_runtime_protocols():
    from room.repository import MessageMongoRepository, RoomMongoRepository

    fake_mongo = MagicMock()
    fake_mongo.collection.return_value = AsyncMock()

    assert isinstance(RoomMongoRepository(mongo=fake_mongo), RoomRepository)
    assert isinstance(MessageMongoRepository(mongo=fake_mongo), MessageRepository)


def test_room_top_level_exports_are_explicit():
    import room
    from room import MessageMongoRepository, RoomFacade, RoomMongoRepository
    from room.repository import MessageMongoRepository as MessageRepositoryExport
    from room.repository import RoomMongoRepository as RoomRepositoryExport

    assert RoomFacade is room.RoomFacade
    assert RoomMongoRepository is room.RoomMongoRepository
    assert MessageMongoRepository is room.MessageMongoRepository
    assert RoomRepositoryExport is RoomMongoRepository
    assert MessageRepositoryExport is MessageMongoRepository
    assert list(room.__all__) == [
        "RoomFacade",
        "RoomMongoRepository",
        "MessageMongoRepository",
    ]


def test_room_packages_are_packaged():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    packages = set(pyproject["tool"]["setuptools"]["packages"])

    assert {"room", "room.repository"}.issubset(packages)


def test_room_import_boundary():
    allowed_roots = set(sys.stdlib_module_names) | {
        "__future__",
        "common",
        "room",
    }
    forbidden_roots = {
        "a2a_adapter",
        "agent",
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

    _assert_import_boundary(Path("room"), allowed_roots, forbidden_roots)


def test_room_container_binds_single_facade_to_all_protocols():
    from common.protocols import (
        RoomHistoryReader,
        RoomManagement,
        RoomMessageStore,
        RoomOwnershipReader,
        RoomRegistry,
    )
    from container import create_room_deps

    fake_mongo = MagicMock()
    fake_mongo.collection.return_value = AsyncMock()
    membership_source = AsyncMock()

    deps = create_room_deps(
        mongo=fake_mongo,
        agent_registry=AsyncMock(),
        membership_source=membership_source,
    )

    assert isinstance(deps.room_registry, RoomRegistry)
    assert isinstance(deps.room_management, RoomManagement)
    assert isinstance(deps.room_message_store, RoomMessageStore)
    assert isinstance(deps.room_history_reader, RoomHistoryReader)
    assert isinstance(deps.room_ownership_reader, RoomOwnershipReader)
    assert deps.room_registry is deps.room_management
    assert deps.room_registry is deps.room_message_store
    assert deps.room_registry is deps.room_history_reader
    assert deps.room_registry is deps.room_ownership_reader


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
