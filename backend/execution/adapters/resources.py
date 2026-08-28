"""Production ResourceMaterializerPort over RoomFiles and the guarded artifact owner."""

from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable
from hashlib import sha256
from typing import Any, Protocol

from common.utils.a2a_artifacts import canonical_data_part_bytes, data_part_digest
from execution.orchestrator.a2a_runtime.models import (
    A2AObservationInboxRecord,
    AgentCallLedgerRecord,
    FrozenCallResourceManifest,
    FrozenCallResourceRef,
    InlineDataArtifact,
    MaterializedResourcePart,
)
from execution.orchestrator.a2a_runtime.resources import (
    BoundedResourceMaterializer,
    ResourceSelectionError,
)
from execution.orchestrator.models import DataPart, PreparedResourceRef


class RoomFileReader(Protocol):
    async def get_bytes(self, file_id: str, *, max_bytes: int) -> bytes | None: ...

    async def get_for_room_file(
        self, room_id: str, file_id: str
    ) -> dict[str, Any] | None: ...


class InlineArtifactReader(Protocol):
    async def load_inline_artifact(
        self, ref_id: str
    ) -> tuple[A2AObservationInboxRecord, InlineDataArtifact] | None: ...


class InboundArtifactWriter(Protocol):
    async def __call__(
        self,
        call: AgentCallLedgerRecord,
        artifact_ref: str,
        observation_id: str,
    ) -> str: ...


class RoomFilesResourceMaterializer:
    """Materialize frozen resource refs from RoomFiles into A2A parts.

    Outbound resources are read from the room-file content store; inbound remote
    artifacts are committed through the epoch-fenced guarded owner (which is the
    only place remote URIs may be fetched).

    ``allowed_input_modes`` and ``deadline_at`` are accepted on the materialize
    boundary but ignored by the per-file loader: ``BoundedResourceMaterializer``
    already enforces the materialization deadline, and bindings pre-filter their
    ``compatible_resource_refs`` so only already-authorized input modes are
    frozen into the manifest.
    """

    def __init__(
        self,
        *,
        room_files: RoomFileReader,
        artifact_writer: InboundArtifactWriter,
        inline_artifact_reader: InlineArtifactReader | None = None,
        context_text_reader: (Callable[[str], Awaitable[str | None]] | None) = None,
        max_outbound_count: int = 20,
        max_outbound_bytes: int = 25 * 1024 * 1024,
        max_outbound_encoded_bytes: int = 34 * 1024 * 1024,
        max_inbound_count: int = 20,
        max_inbound_encoded_bytes: int = 34 * 1024 * 1024,
        max_file_bytes: int = 50 * 1024 * 1024,
    ) -> None:
        self._room_files = room_files
        self._inline_artifact_reader = inline_artifact_reader
        self._context_text_reader = context_text_reader
        self._max_file_bytes = max_file_bytes
        self._bounded = BoundedResourceMaterializer(
            outbound_loader=self._load_outbound,
            inbound_writer=artifact_writer,
            max_outbound_count=max_outbound_count,
            max_outbound_bytes=max_outbound_bytes,
            max_outbound_encoded_bytes=max_outbound_encoded_bytes,
            max_inbound_count=max_inbound_count,
            max_inbound_encoded_bytes=max_inbound_encoded_bytes,
            allow_guarded_remote_artifact_refs=True,
            verify_room_file_ownership=self.verify_room_file_ownership,
        )

    async def verify_room_file_ownership(self, room_id: str, file_id: str) -> None:
        if not await self._room_files.get_for_room_file(room_id, file_id):
            raise ResourceSelectionError("artifact is not owned by the room")

    async def materialize(
        self,
        manifest: FrozenCallResourceManifest,
        *,
        room_id: str,
        room_epoch: int,
        allowed_input_modes: list[str],
        deadline_at: Any,
    ) -> list[MaterializedResourcePart]:
        return await self._bounded.materialize(
            manifest,
            room_id=room_id,
            room_epoch=room_epoch,
            allowed_input_modes=allowed_input_modes,
            deadline_at=deadline_at,
        )

    async def materialize_inbound_artifacts(
        self,
        *,
        call: AgentCallLedgerRecord,
        artifact_refs: list[str],
        observation_id: str,
    ) -> list[str]:
        return await self._bounded.materialize_inbound_artifacts(
            call=call,
            artifact_refs=artifact_refs,
            observation_id=observation_id,
        )

    async def describe_artifact(
        self, ref_id: str, *, room_id: str, room_epoch: int
    ) -> PreparedResourceRef | None:
        inline = await self._read_inline_artifact(ref_id)
        if inline is not None:
            record, artifact, _part = inline
            if record.room_id != room_id or record.room_epoch != room_epoch:
                raise ResourceSelectionError("inline artifact Room ownership changed")
            return PreparedResourceRef(
                ref_id=ref_id,
                kind="artifact",
                source_message_id=record.observation_id,
                mime_type=artifact.mime_type,
                size_bytes=artifact.size_bytes,
                content_digest=artifact.content_digest,
            )
        file_id = _file_id_from_artifact_ref(ref_id)
        metadata = await self._room_files.get_for_room_file(room_id, file_id)
        if metadata is None:
            return None
        digest = str(metadata.get("sha256") or "")
        if not digest:
            raise ResourceSelectionError("artifact file has no content digest")
        return PreparedResourceRef(
            ref_id=ref_id,
            kind="artifact",
            source_message_id=str(metadata.get("source_message_id") or file_id),
            mime_type=str(metadata.get("mime_type") or "application/octet-stream"),
            size_bytes=int(metadata.get("size_bytes") or 0),
            content_digest=digest,
        )

    async def _load_outbound(
        self,
        ref: FrozenCallResourceRef,
        allowed_input_modes: list[str],
        deadline_at: Any,
    ) -> MaterializedResourcePart:
        # Safe to ignore: the bounded materializer enforces ``deadline_at`` and
        # ``allowed_input_modes`` is already enforced at freeze time via the
        # agent binding's input modes (see freeze_call_manifest).
        del allowed_input_modes, deadline_at
        if ref.kind == "context":
            return await self._load_context(ref)
        if ref.kind == "artifact":
            inline = await self._read_inline_artifact(ref.ref_id)
            if inline is not None:
                return self._materialize_inline_artifact(ref, inline)
        file_id = (
            _file_id_from_artifact_ref(ref.ref_id)
            if ref.kind == "artifact"
            else ref.ref_id
        )
        if not await self._room_files.get_for_room_file(ref.room_id, file_id):
            raise ResourceSelectionError(
                f"resource {ref.ref_id!r} is not owned by the room"
            )
        raw = await self._room_files.get_bytes(file_id, max_bytes=self._max_file_bytes)
        if raw is None:
            raise ResourceSelectionError(f"resource {ref.ref_id!r} is unavailable")
        actual_digest = sha256(raw).hexdigest()
        if ref.content_digest and actual_digest != ref.content_digest:
            raise ResourceSelectionError(f"resource {ref.ref_id!r} content changed")
        content_digest = ref.materialization_digest or ref.content_digest
        encoded = base64.b64encode(raw).decode("ascii")
        mime_type = ref.mime_type or "application/octet-stream"
        return MaterializedResourcePart(
            ref_id=ref.ref_id,
            kind="file",
            content_digest=content_digest,
            payload={"name": file_id, "bytes": encoded, "mime_type": mime_type},
            mime_type=mime_type,
        )

    async def _read_inline_artifact(
        self, ref_id: str
    ) -> tuple[A2AObservationInboxRecord, InlineDataArtifact, DataPart] | None:
        if self._inline_artifact_reader is None:
            return None
        resolved = await self._inline_artifact_reader.load_inline_artifact(ref_id)
        if resolved is None:
            return None
        record, artifact = resolved
        part = record.observation.content[artifact.content_index]
        if not isinstance(part, DataPart):
            raise ResourceSelectionError("inline artifact source is not a DataPart")
        canonical = canonical_data_part_bytes(
            part.data, mime_type=part.mime_type, metadata=part.metadata
        )
        if (
            data_part_digest(canonical) != artifact.content_digest
            or len(canonical) != artifact.size_bytes
            or part.mime_type != artifact.mime_type
        ):
            raise ResourceSelectionError("inline artifact content changed")
        return record, artifact, part

    @staticmethod
    def _materialize_inline_artifact(
        ref: FrozenCallResourceRef,
        inline: tuple[A2AObservationInboxRecord, InlineDataArtifact, DataPart],
    ) -> MaterializedResourcePart:
        record, artifact, part = inline
        if record.room_id != ref.room_id or record.room_epoch != ref.room_epoch:
            raise ResourceSelectionError("inline artifact Room ownership changed")
        if ref.content_digest and ref.content_digest != artifact.content_digest:
            raise ResourceSelectionError("inline artifact digest changed")
        if ref.mime_type and ref.mime_type != artifact.mime_type:
            raise ResourceSelectionError("inline artifact MIME changed")
        return MaterializedResourcePart(
            ref_id=ref.ref_id,
            kind="data",
            content_digest=artifact.content_digest,
            payload=part.data,
            mime_type=part.mime_type,
            metadata=part.metadata,
        )

    async def _load_context(
        self, ref: FrozenCallResourceRef
    ) -> MaterializedResourcePart:
        if self._context_text_reader is None:
            raise ResourceSelectionError("context resource reader is unavailable")
        text = await self._context_text_reader(ref.source_message_id)
        if text is None:
            raise ResourceSelectionError(f"resource {ref.ref_id!r} is unavailable")
        digest = sha256(text.encode("utf-8")).hexdigest()
        if ref.content_digest and digest != ref.content_digest:
            raise ResourceSelectionError(f"resource {ref.ref_id!r} content changed")
        return MaterializedResourcePart(
            ref_id=ref.ref_id,
            kind="text",
            content_digest=(ref.materialization_digest or ref.content_digest or digest),
            payload=text,
            mime_type=ref.mime_type or "text/plain",
        )


def _file_id_from_artifact_ref(ref_id: str) -> str:
    """Extract the durable room file id from an owned artifact content URL.

    Inbound artifacts are committed through the epoch-fenced owner, which
    returns ``{prefix}/{file_id}/content``. For any ref that does not match
    that shape, fall back to treating the ref verbatim as a file id.
    """
    parts = ref_id.rstrip("/").rsplit("/", 2)
    if len(parts) == 3 and parts[-1] == "content" and parts[1]:
        return parts[1]
    return ref_id


__all__ = ["RoomFilesResourceMaterializer"]
