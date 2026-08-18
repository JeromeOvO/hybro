"""Private parser for typed A2A interaction metadata."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from pydantic import ValidationError

from common.a2a_constants import HYBRO_A2A_INTERACTION_METADATA_KEY
from common.dto.hitl import A2AInteractionSpec
from common.types import Task, TaskStatusUpdateEvent


def freeze_interaction_metadata(value: Any) -> Any:
    """Recursively freeze untrusted metadata for private evidence integrity."""

    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): freeze_interaction_metadata(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(freeze_interaction_metadata(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(freeze_interaction_metadata(item) for item in value)
    return value


class A2AInteractionDisposition(StrEnum):
    """Classification of the namespaced metadata at its one accepted location."""

    TYPED = "typed"
    UNTYPED = "untyped"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class A2AInteractionParseResult:
    disposition: A2AInteractionDisposition
    raw_metadata: Mapping[str, object]
    spec: A2AInteractionSpec | None = None
    validation_error: str | None = None


def extract_a2a_interaction_spec(
    source: Task | TaskStatusUpdateEvent,
) -> A2AInteractionParseResult:
    """Parse only ``status.message.metadata`` from a task or status event.

    Task/context identifiers deliberately are not returned. Callers must bind
    continuation identity from trusted transport fields on ``source``.
    """

    if not isinstance(source, (Task, TaskStatusUpdateEvent)):
        raise TypeError("source must be Task or TaskStatusUpdateEvent")

    message = source.status.message
    metadata = message.metadata if message is not None else None
    raw_metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
    frozen_metadata = freeze_interaction_metadata(raw_metadata)

    if HYBRO_A2A_INTERACTION_METADATA_KEY not in raw_metadata:
        return A2AInteractionParseResult(
            disposition=A2AInteractionDisposition.UNTYPED,
            raw_metadata=frozen_metadata,
        )

    try:
        spec = A2AInteractionSpec.model_validate(
            raw_metadata[HYBRO_A2A_INTERACTION_METADATA_KEY]
        )
    except (TypeError, ValidationError, ValueError) as exc:
        return A2AInteractionParseResult(
            disposition=A2AInteractionDisposition.INVALID,
            raw_metadata=frozen_metadata,
            validation_error=str(exc),
        )

    return A2AInteractionParseResult(
        disposition=A2AInteractionDisposition.TYPED,
        raw_metadata=frozen_metadata,
        spec=spec,
    )


__all__ = [
    "A2AInteractionDisposition",
    "A2AInteractionParseResult",
    "extract_a2a_interaction_spec",
    "freeze_interaction_metadata",
]
