import ast
import inspect
import sys
import tomllib
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
    from datetime import datetime, timezone

    from context_memory import ContextMemoryFacade

    return ContextMemoryFacade(
        memory_repository=FakeMemoryRepository(),
        content_repository=FakeContentRepository(),
        room_history_reader=FakeRoomHistoryReader(),
        vector=FakeVector(),
        llm_provider=FakeLLM(),
        id_factory=lambda: "id-1",
        now=lambda: datetime.now(timezone.utc),
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
    from context_memory.repository import MemoryMongoRepository as RepoMemoryMongoRepository

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

    for path in Path("context_memory").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            root = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".", 1)[0]
                    assert root in allowed_roots and root not in forbidden, (
                        path,
                        alias.name,
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".", 1)[0]
                assert root in allowed_roots and root not in forbidden, (
                    path,
                    node.module,
                )
