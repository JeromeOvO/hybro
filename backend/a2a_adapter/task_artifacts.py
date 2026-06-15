from __future__ import annotations

from typing import Any
from uuid import uuid4

from common.types import Artifact, DataPart, FilePart, Part


def materialize_non_text_parts_as_artifact(
    task: Any,
    non_text_parts: list[dict[str, Any]],
) -> None:
    if not non_text_parts:
        return

    wrapped_parts: list[Part] = []
    for part in non_text_parts:
        kind = part.get("kind")
        try:
            if kind == "file":
                wrapped_parts.append(Part(root=FilePart(**part)))
            elif kind == "data":
                wrapped_parts.append(Part(root=DataPart(**part)))
        except Exception:
            continue

    if not wrapped_parts:
        return

    if getattr(task, "artifacts", None) is None:
        task.artifacts = []

    task.artifacts.append(
        Artifact(
            artifact_id=uuid4().hex,
            parts=wrapped_parts,
            name="streaming-multimodal",
            metadata={"source": "streaming_non_text"},
        )
    )


__all__ = ["materialize_non_text_parts_as_artifact"]
