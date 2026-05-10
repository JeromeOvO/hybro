from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from common.protocols import MongoDAL


@dataclass(frozen=True)
class _IndexRegistration:
    module_name: str
    collection: str
    index_spec: list[tuple]
    kwargs: dict[str, Any] = field(default_factory=dict)


class IndexRegistryImpl:
    """In-memory index registration with Mongo-backed creation."""

    def __init__(self, mongo: MongoDAL) -> None:
        self._mongo = mongo
        self._registrations: list[_IndexRegistration] = []

    def register(
        self,
        module_name: str,
        collection: str,
        index_spec: list[tuple],
        **kwargs,
    ) -> None:
        self._registrations.append(
            _IndexRegistration(
                module_name=module_name,
                collection=collection,
                index_spec=index_spec,
                kwargs=kwargs,
            )
        )

    async def ensure_all(self) -> None:
        errors: list[tuple[str, Exception]] = []
        for registration in self._registrations:
            try:
                collection = self._mongo.collection(registration.collection)
                await collection.create_index(
                    registration.index_spec,
                    **registration.kwargs,
                )
            except Exception as exc:
                errors.append(
                    (f"{registration.module_name}:{registration.collection}", exc)
                )
        if errors:
            msg = "; ".join(f"{name}: {exc}" for name, exc in errors)
            raise RuntimeError(f"Index creation failures: {msg}")
