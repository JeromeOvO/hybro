import ast
import math
from pathlib import Path

import pytest

from extensions.vector_store import (
    VectorRecord,
    VectorSearchResult,
    VectorStore,
    VectorStoreError,
)


class FakeVectorStore:
    def __init__(self):
        self.by_namespace: dict[str, dict[str, VectorRecord]] = {}

    async def search(self, namespace, query_vector, top_k):
        try:
            scored = [
                VectorSearchResult(
                    id=record.id,
                    score=sum(
                        left * right
                        for left, right in zip(
                            record.vector, query_vector, strict=True
                        )
                    ),
                    metadata=record.metadata,
                )
                for record in self.by_namespace.get(namespace, {}).values()
            ]
        except ValueError as exc:
            raise VectorStoreError("vector dimensions do not match") from exc
        return sorted(scored, key=lambda result: -result.score)[:top_k]

    async def upsert(self, namespace, records):
        target = self.by_namespace.setdefault(namespace, {})
        target.update({record.id: record for record in records})

    async def delete(self, namespace, ids):
        target = self.by_namespace.setdefault(namespace, {})
        for record_id in ids:
            target.pop(record_id, None)

    async def ping(self):
        return True


async def test_vector_store_contract_is_namespace_scoped_and_higher_is_better():
    store = FakeVectorStore()
    assert isinstance(store, VectorStore)
    await store.upsert(
        "agents",
        [
            VectorRecord("a-low", [0.2, 0.0], {"kind": "agent"}),
            VectorRecord("a-high", [1.0, 0.0], {"kind": "agent"}),
        ],
    )
    await store.upsert("memory", [VectorRecord("m1", [1.0, 0.0])])
    assert [result.id for result in await store.search("agents", [1.0, 0.0], 5)] == [
        "a-high",
        "a-low",
    ]
    agent_results = await store.search("agents", [1.0, 0.0], 5)
    assert agent_results[0].score > agent_results[1].score
    assert math.isclose(agent_results[0].score, 1.0)
    assert [result.id for result in await store.search("memory", [1.0, 0.0], 5)] == [
        "m1"
    ]
    await store.delete("agents", ["a-low", "a-high"])
    assert await store.search("agents", [1.0, 0.0], 5) == []
    assert [result.id for result in await store.search("memory", [1.0, 0.0], 5)] == [
        "m1"
    ]
    assert await store.ping() is True


async def test_vector_store_failures_use_provider_neutral_error_contract():
    store = FakeVectorStore()
    await store.upsert("agents", [VectorRecord("a1", [1.0, 0.0])])

    with pytest.raises(VectorStoreError, match="dimensions do not match") as raised:
        await store.search("agents", [1.0], 5)

    assert isinstance(raised.value.__cause__, ValueError)


def test_vector_store_extension_has_no_runtime_consumer_or_container_binding():
    violations = []
    runtime_paths = [
        path
        for root in Path(".").iterdir()
        if root.is_dir() and not root.name.startswith(".") and root.name != "tests"
        for path in root.rglob("*.py")
        if root.name != "extensions"
    ]
    runtime_paths.extend([Path("container.py"), Path("main.py")])
    for path in runtime_paths:
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "extensions.vector_store":
                violations.append(str(path))
            if isinstance(node, ast.Import):
                if any(alias.name == "extensions.vector_store" for alias in node.names):
                    violations.append(str(path))

    assert violations == []
    assert "vector_store" not in Path("container.py").read_text()
    assert "VectorStore" not in Path("common/protocols/__init__.py").read_text()


def test_removed_vector_provider_is_absent_from_runtime_config_and_dependencies():
    provider_name = "pine" + "cone"
    for path in (
        Path("pyproject.toml"),
        Path("uv.lock"),
        Path(".env.example"),
    ):
        assert provider_name not in path.read_text().casefold()

    runtime_roots = [
        path
        for root in Path(".").iterdir()
        if root.is_dir()
        and not root.name.startswith(".")
        and root.name not in {"tests", "docs", "extensions"}
        for path in root.rglob("*")
        if path.is_file() and path.suffix in {".py", ".toml"}
    ]
    runtime_roots.extend([Path("container.py"), Path("main.py")])
    violations = [
        str(path)
        for path in runtime_roots
        if provider_name in path.read_text().casefold()
    ]
    assert violations == []
