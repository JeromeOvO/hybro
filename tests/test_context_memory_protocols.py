import ast
import inspect
import sys
import tomllib
from datetime import UTC
from pathlib import Path

import pytest

from common.protocols import (
    ContextAssembler,
    MemoryManager,
    MemoryProjector,
    MemoryRepository,
)


class FakeMongo:
    def collection(self, name: str):
        return object()


class FakeMemoryRepository:
    async def get_room_memory(self, room_id: str) -> dict | None:
        return None

    async def upsert_room_memory(self, room_id: str, memory: dict) -> None:
        return None

    async def get_user_memories(self, user_id: str) -> list[dict]:
        return []

    async def delete_room_memory(self, room_id: str) -> bool:
        return True

    async def create_room_memory(self, memory: dict) -> str:
        return memory["memory_id"]

    async def ensure_room_memory(self, room_id: str, defaults: dict) -> dict:
        return {"room_id": room_id, **defaults}

    async def get_room_memory_by_memory_id(self, memory_id: str) -> dict | None:
        return None

    async def update_room_memory_by_room_id(self, room_id: str, updates: dict) -> bool:
        return True

    async def update_room_memory_by_memory_id(self, memory_id: str, updates: dict) -> bool:
        return True

    async def delete_room_memory_by_memory_id(self, memory_id: str) -> bool:
        return True

    async def push_and_trim_conversation_turn(
        self,
        room_id: str,
        turn: dict,
        *,
        max_turns: int,
        summary_stub: str,
        max_summary_chars: int,
    ) -> tuple[bool, bool]:
        return True, True

    async def push_and_trim_conversation_turn_if_absent(
        self,
        room_id: str,
        turn: dict,
        *,
        turn_id: str,
        max_turns: int,
        summary_stub: str,
        max_summary_chars: int,
    ) -> tuple[bool, bool, bool]:
        return True, True, False

    async def update_turn_notes(self, room_id: str, turn_id: str, turn_notes: dict) -> bool:
        return True

    async def get_room_summary_projection(self, room_id: str) -> dict | None:
        return None

    async def update_room_summary_atomic(
        self,
        room_id: str,
        room_summary: dict,
        *,
        new_facts: list[dict] | None = None,
        max_facts: int = 50,
    ) -> bool:
        return True

    async def compact_turns_bulk(self, room_id: str, compacted_turns: list[dict]) -> bool:
        return True

    async def list_room_ids_with_memory(self, limit: int | None = None) -> list[str]:
        return []


class FakeContentRepository:
    async def upsert_full_content(self, **kwargs) -> str:
        return kwargs["document_id"]

    async def get_content_by_document_id(self, document_id: str) -> dict | None:
        return None

    async def get_content_by_turn_id(self, room_id: str, turn_id: str) -> dict | None:
        return None

    async def delete_content_by_turn_id(self, room_id: str, turn_id: str) -> bool:
        return True

    async def delete_content_by_room_id(self, room_id: str) -> int:
        return 0

    async def get_content_stats_for_room(self, room_id: str) -> dict:
        return {"room_id": room_id}

    async def text_search(self, room_id: str, query: str, limit: int = 50) -> list[dict]:
        return []

    async def hydrate_turn_notes(self, room_id: str, turn_ids: list[str]) -> list[dict]:
        return []


class FakeRoomHistoryReader:
    async def get_messages_for_room(self, room_id: str, limit: int, before=None):
        return []

    async def get_messages_by_ids(self, message_ids: list[str]):
        return []

    async def get_message_thread(self, parent_message_id: str):
        return []


class FakeVector:
    async def search(self, index, vector, top_k, filter=None):
        return []

    async def upsert(self, index, records):
        return None

    async def delete(self, index, ids):
        return None

    async def delete_by_filter(self, index, filter):
        return None

    async def ping(self):
        return True


class FakeLLM:
    async def generate(self, request):
        return None

    async def generate_structured(self, messages, schema, model=None):
        return type("Response", (), {"data": {}})()

    async def embed(self, text):
        return [0.1]

    async def embed_batch(self, texts):
        return [[0.1] for _ in texts]


def _facade():
    from datetime import datetime

    from context_memory import ContextMemoryFacade

    return ContextMemoryFacade(
        memory_repository=FakeMemoryRepository(),
        content_repository=FakeContentRepository(),
        room_history_reader=FakeRoomHistoryReader(),
        vector=FakeVector(),
        llm_provider=FakeLLM(),
        id_factory=lambda: "id-1",
        now=lambda: datetime.now(UTC),
    )


def test_context_memory_runtime_protocol_conformance():
    from common.protocols import ContentStorageRepository
    from context_memory import ContentStorageMongoRepository, MemoryMongoRepository

    facade = _facade()

    assert isinstance(facade, ContextAssembler)
    assert isinstance(facade, MemoryManager)
    assert isinstance(facade, MemoryProjector)
    assert isinstance(MemoryMongoRepository(mongo=FakeMongo()), MemoryRepository)
    assert isinstance(
        ContentStorageMongoRepository(mongo=FakeMongo()), ContentStorageRepository
    )


def test_context_memory_exports_are_stable():
    import context_memory
    from context_memory import (
        ContentStorageMongoRepository,
        ContextMemoryFacade,
        MemoryMongoRepository,
    )
    from context_memory.repository import (
        ContentStorageMongoRepository as RepoContentStorageMongoRepository,
    )
    from context_memory.repository import (
        MemoryMongoRepository as RepoMemoryMongoRepository,
    )

    assert ContextMemoryFacade.__name__ == "ContextMemoryFacade"
    assert MemoryMongoRepository is RepoMemoryMongoRepository
    assert ContentStorageMongoRepository is RepoContentStorageMongoRepository
    assert context_memory.__all__ == [
        "ContextMemoryFacade",
        "MemoryMongoRepository",
        "ContentStorageMongoRepository",
    ]


def test_context_memory_packages_are_listed():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    packages = set(pyproject["tool"]["setuptools"]["packages"])

    assert {"context_memory", "context_memory.repository"}.issubset(packages)


def test_context_memory_protocol_method_sets():
    from common import protocols

    def public_methods(protocol):
        return {
            name
            for name, member in protocol.__dict__.items()
            if inspect.isfunction(member) and not name.startswith("_")
        }

    assert public_methods(protocols.ContentStorageRepository) == {
        "upsert_full_content",
        "get_content_by_document_id",
        "get_content_by_turn_id",
        "delete_content_by_turn_id",
        "delete_content_by_room_id",
        "get_content_stats_for_room",
        "text_search",
        "hydrate_turn_notes",
    }


def test_context_memory_facade_uses_settings_compaction_concurrency(monkeypatch):
    from common.config import settings
    from container import create_context_memory_facade

    monkeypatch.setattr(settings, "compaction_concurrency", 11)

    facade = create_context_memory_facade(
        mongo=FakeMongo(),
        vector=FakeVector(),
        llm_provider=FakeLLM(),
        room_history_reader=FakeRoomHistoryReader(),
    )

    assert facade.compaction_config.concurrency == 11


def test_context_memory_config_defaults_read_common_settings(monkeypatch):
    from common.config import settings
    from context_memory.config import (
        CompactionConfig,
        MemorySearchConfig,
        TokenBudgetConfig,
    )

    monkeypatch.setattr(settings, "context_model_window", 12345)
    monkeypatch.setattr(settings, "context_system_prompt_tokens", 101)
    monkeypatch.setattr(settings, "context_tool_schema_tokens", 102)
    monkeypatch.setattr(settings, "context_response_reserve_tokens", 103)
    monkeypatch.setattr(settings, "context_room_pct", 0.2)
    monkeypatch.setattr(settings, "context_history_pct", 0.5)
    monkeypatch.setattr(settings, "context_task_pct", 0.3)
    monkeypatch.setattr(settings, "compaction_enabled", False)
    monkeypatch.setattr(settings, "compaction_max_full_turns", 7)
    monkeypatch.setattr(settings, "compaction_max_total_tokens", 777)
    monkeypatch.setattr(settings, "compaction_preserve_recent", 3)
    monkeypatch.setattr(settings, "compaction_content_ttl_days", 9)
    monkeypatch.setattr(settings, "compaction_concurrency", 4)
    monkeypatch.setattr(settings, "memory_search_enabled", False)
    monkeypatch.setattr(settings, "memory_search_vector_weight", 0.4)
    monkeypatch.setattr(settings, "memory_search_keyword_weight", 0.6)
    monkeypatch.setattr(settings, "memory_search_index_name", "settings-index")

    token_budget = TokenBudgetConfig()
    compaction = CompactionConfig()
    search = MemorySearchConfig()

    assert token_budget.model_context_window == 12345
    assert token_budget.system_prompt == 101
    assert token_budget.tool_schemas == 102
    assert token_budget.response_reserve == 103
    assert token_budget.room_context_pct == 0.2
    assert token_budget.conversation_history_pct == 0.5
    assert token_budget.current_task_pct == 0.3
    assert compaction.enabled is False
    assert compaction.max_full_turns == 7
    assert compaction.max_total_tokens == 777
    assert compaction.preserve_recent_turns == 3
    assert compaction.content_ttl_days == 9
    assert compaction.concurrency == 4
    assert search.enabled is False
    assert search.vector_weight == 0.4
    assert search.keyword_weight == 0.6
    assert search.index_name == "settings-index"


def test_context_memory_setting_helper_does_not_swallow_import_failures(monkeypatch):
    import builtins
    import importlib

    context_memory_config = importlib.import_module("context_memory.config")

    real_import = builtins.__import__

    def failing_import(name, *args, **kwargs):
        if name == "common.config":
            raise RuntimeError("settings validation failed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", failing_import)

    with pytest.raises(RuntimeError, match="settings validation failed"):
        context_memory_config._setting("context_model_window", 128000)


def test_room_delete_has_no_stale_direct_context_memory_cleanup():
    from app_shell.room_runtime import RoomServices

    source = inspect.getsource(RoomServices.delete_room_by_room_id)

    assert "room_memories_collection" not in source
    assert "conversation_content_collection" not in source


def test_room_delete_logs_when_context_memory_cleanup_is_unbound():
    from app_shell.room_runtime import RoomServices

    source = inspect.getsource(RoomServices._cleanup_context_memory_for_room)

    assert "Context & Memory cleanup skipped" in source
    assert "_context_memory_manager is None" in source


def test_message_write_flows_do_not_call_context_memory_write_shims():
    forbidden = {
        "initialize_or_update_room_memory",
        "add_agent_response_to_memory",
    }
    checked_paths = [
        Path("room/compat/runtime.py"),
        Path("execution/orchestration/queue_executor.py"),
        Path("execution/orchestration/supervisor_executor.py"),
        Path("execution/orchestration/room_message_center.py"),
    ]
    violations: list[str] = []
    for path in checked_paths:
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in forbidden:
                    violations.append(f"{path}:{node.lineno}:{node.func.attr}")

    assert violations == []


def test_execution_startup_adapter_does_not_inject_agent_memory_write_shim():
    source = Path("container.py").read_text()

    for shim in {
        "initialize_or_update_room_memory",
        "add_agent_response_to_memory",
    }:
        assert f"room_memory_service.{shim}" not in source
        assert f"{shim}=(" not in source


def test_context_memory_import_boundary():
    forbidden = {
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
        "openai",
        "pinecone",
        "pymongo",
        "room",
        "services",
    }
    allowed_stdlib = set(sys.stdlib_module_names) | {"__future__"}
    allowed_roots = allowed_stdlib | {"common", "context_memory"}
    protocol_legacy_model_imports = {
        "models.request",
        "models.response",
    }
    path_legacy_compat_imports = {
        Path("context_memory/protocols.py"): protocol_legacy_model_imports,
        Path("context_memory/compat/runtime.py"): {
            "llm_gateway.errors",
            "models.error",
            "models.memory",
            "models.request",
            "models.response",
        },
    }

    for path in Path("context_memory").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            root = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in path_legacy_compat_imports.get(path, set()):
                        continue
                    root = alias.name.split(".", 1)[0]
                    assert root in allowed_roots and root not in forbidden, (
                        path,
                        alias.name,
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module in path_legacy_compat_imports.get(path, set()):
                    continue
                root = node.module.split(".", 1)[0]
                assert root in allowed_roots and root not in forbidden, (
                    path,
                    node.module,
                )


def test_non_protocol_helper_call_boundary():
    allowed_call_sites = {
        "context_memory/compat/context_assembly.py": {
            "assemble_supervisor_context_from_memory",
            "assemble_agent_execution_context_from_memory",
        },
        "app_shell/memory_service.py": {
            "legacy_create_room_memory",
            "legacy_get_room_memory_by_room_id",
            "legacy_get_room_memory_by_memory_id",
            "legacy_update_room_memory_by_room_id",
            "legacy_update_room_memory_by_memory_id",
            "legacy_delete_room_memory_by_room_id",
            "legacy_delete_room_memory_by_memory_id",
            "initialize_or_update_room_memory",
            "add_agent_response_to_memory",
            "add_synthesis_to_history",
            "update_room_summary",
        },
        "app_shell/memory_search_service.py": {
            "legacy_search",
            "index_turn_for_search",
            "delete_room_index",
        },
        "app_shell/compaction_service.py": {
            "should_compact",
            "compact_if_needed",
            "compact_room_memory",
            "expand_turn_content",
            "expand_turn_content_from_turn",
            "fetch_turn_content",
            "get_compaction_stats",
        },
        "context_memory/events.py": {"project_message_for_event"},
    }
    helper_names = set().union(*allowed_call_sites.values())
    path_allowed_helpers = {
        Path(path): helpers for path, helpers in allowed_call_sites.items()
    }

    violations = []
    for path in Path(".").rglob("*.py"):
        if (
            path.parts[0] in {"context_memory", "tests"}
                or path in {Path("container.py"), Path("main.py")}
                or ".venv" in path.parts
                or ".worktrees" in path.parts
            ):
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = None
            receiver = ""
            if isinstance(node.func, ast.Attribute):
                name = node.func.attr
                receiver = ast.unparse(node.func.value)
            elif isinstance(node.func, ast.Name):
                name = node.func.id
            if name not in helper_names:
                continue
            if isinstance(node.func, ast.Attribute) and "facade" not in receiver:
                continue
            if name not in path_allowed_helpers.get(path, set()):
                violations.append(f"{path}:{node.lineno}:{name}")

    assert violations == []
