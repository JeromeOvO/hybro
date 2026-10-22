"""Index helpers shared by the repositories that own each collection."""

from __future__ import annotations

from typing import Any

from common.protocols import MongoCollection
from common.utils.logger import get_logger

logger = get_logger(__name__)


async def create_index(
    collection: MongoCollection,
    collection_name: str,
    keys: Any,
    *,
    name: str,
    unique: bool = False,
    critical: bool = False,
    **kwargs: Any,
) -> bool:
    """Create one index; a failed ``critical`` index aborts startup."""
    try:
        await collection.create_index(keys, unique=unique, name=name, **kwargs)
        return True
    except Exception as exc:
        if critical:
            logger.error(
                "Critical index creation failed for %s.%s",
                collection_name,
                name,
                exc_info=True,
            )
            raise RuntimeError(
                f"Critical index creation failed for {collection_name}.{name}"
            ) from exc
        logger.warning(
            "Index creation failed for %s.%s",
            collection_name,
            name,
            exc_info=True,
        )
        return False


async def ensure_text_index(
    collection: MongoCollection,
    *,
    name: str,
    weights: dict[str, int],
) -> bool:
    """Keep exactly one text index with ``weights``; report readiness."""
    try:
        existing = await collection.index_information()
        desired_weights = dict(sorted(weights.items()))
        matching = existing.get(name)
        matching_keys = tuple(
            (key, direction) for key, direction in ((matching or {}).get("key") or [])
        )
        valid_text_keys = {
            (("_fts", "text"), ("_ftsx", 1)),
            tuple((field, "text") for field in weights),
        }
        if (
            matching
            and dict(sorted((matching.get("weights") or {}).items())) == desired_weights
            and matching_keys in valid_text_keys
        ):
            return True
        for index_name, spec in existing.items():
            keys = spec.get("key") or []
            if spec.get("weights") or any(key == "_fts" for key, _ in keys):
                await collection.drop_index(index_name)
        await collection.create_index(
            [(field, "text") for field in weights],
            name=name,
            unique=False,
            weights=weights,
        )
        return True
    except Exception:
        logger.warning("Search index creation failed for %s", name, exc_info=True)
        return False


__all__ = ["create_index", "ensure_text_index"]
