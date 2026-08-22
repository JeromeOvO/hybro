"""Resource catalog owned by the context & memory module.

The orchestrator consumes this catalog once per turn via ``list_resources`` so
the ref enum it exposes to the model reflects the *live* set of resources
(user text, attachments, agent artifacts) rather than a run-start frozen
snapshot. Assembly (which inputs become which refs) lives here; the orchestrator
only translates entries into its own durable ``RunResourceManifestSnapshot``.

A ref id is deterministic so re-assembling the same turn yields the same ref:

- user text  -> ``ctx:message:{user_message_id}``  (kind ``context``)
- attachment -> the stored ``file_id``              (kind ``attachment``)
- artifact   -> the artifact reference verbatim      (kind ``artifact``)
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Literal, Protocol

ResourceKind = Literal["context", "artifact", "attachment"]


@dataclass(frozen=True, slots=True)
class ResourceCatalogEntry:
    """One live resource the model may reference by id.

    Field shape intentionally mirrors the orchestrator's ``PreparedResourceRef``
    so the orchestrator can convert with no information loss.
    """

    ref_id: str
    kind: ResourceKind
    source_message_id: str
    mime_type: str | None = None
    size_bytes: int = 0
    content_digest: str = ""


@dataclass(frozen=True, slots=True)
class AttachmentResource:
    file_id: str
    mime_type: str | None
    size_bytes: int
    content_digest: str


@dataclass(frozen=True, slots=True)
class ResourceCatalogSource:
    """Inputs available to assemble the catalog for a turn."""

    user_message_id: str
    user_text: str | None = None
    attachments: Sequence[AttachmentResource] = field(default_factory=tuple)
    artifact_refs: Sequence[str] = field(default_factory=tuple)


class ResourceCatalogPort(Protocol):
    """Per-turn live resource listing consumed by the orchestrator."""

    async def list_resources(
        self, *, room_id: str, room_epoch: int
    ) -> list[ResourceCatalogEntry]: ...


def user_text_ref_id(user_message_id: str) -> str:
    return f"ctx:message:{user_message_id}"


def assemble_resource_catalog(
    source: ResourceCatalogSource,
) -> list[ResourceCatalogEntry]:
    """Assemble the live catalog from a turn's inputs.

    User text becomes a single ``context`` ref; attachments and already-produced
    artifacts are emitted as their own refs. Ordering is stable and every ref id
    is deduplicated while preserving first-seen order.
    """
    entries: list[ResourceCatalogEntry] = []
    seen: set[str] = set()

    def add(entry: ResourceCatalogEntry) -> None:
        if entry.ref_id in seen:
            return
        seen.add(entry.ref_id)
        entries.append(entry)

    if source.user_text is not None and source.user_text.strip():
        text = source.user_text
        add(
            ResourceCatalogEntry(
                ref_id=user_text_ref_id(source.user_message_id),
                kind="context",
                source_message_id=source.user_message_id,
                mime_type="text/plain",
                size_bytes=len(text.encode("utf-8")),
                content_digest=sha256(text.encode("utf-8")).hexdigest(),
            )
        )

    for attachment in source.attachments:
        add(
            ResourceCatalogEntry(
                ref_id=attachment.file_id,
                kind="attachment",
                source_message_id=source.user_message_id,
                mime_type=attachment.mime_type,
                size_bytes=attachment.size_bytes,
                content_digest=attachment.content_digest,
            )
        )

    for artifact_ref in source.artifact_refs:
        if not artifact_ref:
            continue
        add(
            ResourceCatalogEntry(
                ref_id=artifact_ref,
                kind="artifact",
                source_message_id=source.user_message_id,
                mime_type=None,
                size_bytes=0,
                content_digest="",
            )
        )

    return entries


__all__ = [
    "AttachmentResource",
    "ResourceCatalogEntry",
    "ResourceCatalogPort",
    "ResourceCatalogSource",
    "ResourceKind",
    "assemble_resource_catalog",
    "user_text_ref_id",
]
