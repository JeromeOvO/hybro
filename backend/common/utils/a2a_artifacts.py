"""Deterministic identities for inline structured A2A artifacts."""

from __future__ import annotations

import json
from hashlib import sha256


def canonical_data_part_bytes(
    data: dict[str, object] | list[object],
    *,
    mime_type: str = "application/json",
    metadata: dict[str, object] | None = None,
) -> bytes:
    """Serialize one structured A2A part for stable hashing and size checks."""

    return json.dumps(
        {"data": data, "metadata": metadata, "mime_type": mime_type},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def inline_data_artifact_identity(
    *,
    observation_id: str,
    artifact_id: str | None,
    artifact_index: int,
    part_index: int,
    content_digest: str,
) -> str:
    """Return a short replay-stable Ref scoped to one observed A2A data part."""

    source_artifact = artifact_id or f"ordinal:{artifact_index}"
    identity = "|".join(
        (
            observation_id,
            source_artifact,
            str(artifact_index),
            str(part_index),
            content_digest,
        )
    )
    return f"art_{sha256(identity.encode('utf-8')).hexdigest()[:32]}"


def data_part_digest(payload: bytes) -> str:
    return sha256(payload).hexdigest()


__all__ = [
    "canonical_data_part_bytes",
    "data_part_digest",
    "inline_data_artifact_identity",
]
