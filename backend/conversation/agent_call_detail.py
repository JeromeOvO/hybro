"""Private detail projection for room-authorized opaque canonical Agent cards."""

from __future__ import annotations

import json
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

_ROOM_FILE_REF_RE = re.compile(r"/api/v1/files/(?P<file_id>[A-Za-z0-9_-]+)/content")


class CanonicalArtifactDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_ref: str
    file_id: str | None = None
    name: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)


class CanonicalTextPart(BaseModel):
    """Room-authorized projection of an A2A TextPart."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["text"] = "text"
    text: str


class CanonicalDataPart(BaseModel):
    """Room-authorized projection of an A2A DataPart without private metadata."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["data"] = "data"
    data: dict[str, Any] | list[Any]


CanonicalAgentCallPart = Annotated[
    CanonicalTextPart | CanonicalDataPart,
    Field(discriminator="kind"),
]


class CanonicalAgentCallDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    public_call_id: str
    status: str
    # Rolling-deploy compatibility for clients predating typed ``parts``.
    output: str
    parts: list[CanonicalAgentCallPart] = Field(default_factory=list)
    artifacts: list[CanonicalArtifactDescriptor] = Field(default_factory=list)


class CanonicalAgentCallDetailService:
    """Read private Tool output without exposing backend call/provider identity."""

    def __init__(
        self,
        run_store: Any,
        artifact_metadata_reader: Any | None = None,
    ) -> None:
        self._run_store = run_store
        self._artifact_metadata_reader = artifact_metadata_reader

    async def _artifact_descriptor(
        self,
        *,
        room_id: str,
        artifact_ref: str,
    ) -> CanonicalArtifactDescriptor:
        match = _ROOM_FILE_REF_RE.fullmatch(artifact_ref)
        file_id = match.group("file_id") if match else None
        if file_id is None or self._artifact_metadata_reader is None:
            return CanonicalArtifactDescriptor(artifact_ref=artifact_ref)
        try:
            metadata = await self._artifact_metadata_reader.get_for_room_file(
                room_id,
                file_id,
            )
        except Exception:
            metadata = None
        if not isinstance(metadata, dict):
            return CanonicalArtifactDescriptor(artifact_ref=artifact_ref)
        name = metadata.get("file_name")
        mime_type = metadata.get("mime_type")
        size_bytes = metadata.get("size_bytes")
        return CanonicalArtifactDescriptor(
            artifact_ref=artifact_ref,
            file_id=file_id,
            name=name if isinstance(name, str) and name else None,
            mime_type=(mime_type if isinstance(mime_type, str) and mime_type else None),
            size_bytes=(
                size_bytes
                if isinstance(size_bytes, int)
                and not isinstance(size_bytes, bool)
                and size_bytes >= 0
                else None
            ),
        )

    async def get(
        self,
        *,
        room_id: str,
        run_id: str,
        public_call_id: str,
    ) -> CanonicalAgentCallDetail | None:
        run = await self._run_store.load(run_id)
        if run is None or run.room_id != room_id or run.lifecycle_family != "canonical":
            return None
        entry = next(
            (
                item
                for batch in run.tool_batches
                for item in batch.entries
                if item.opaque_public_call_id == public_call_id
            ),
            None,
        )
        if entry is None or entry.buffered_terminal_result is None:
            return None
        result = entry.buffered_terminal_result
        output_parts: list[str] = []
        typed_parts: list[CanonicalAgentCallPart] = []
        for part in result.content:
            kind = getattr(part, "kind", None)
            if kind == "text":
                text = getattr(part, "text", None)
                if isinstance(text, str):
                    typed_parts.append(CanonicalTextPart(text=text))
                    if text:
                        output_parts.append(text)
            elif kind == "data":
                data = getattr(part, "data", None)
                if isinstance(data, (dict, list)):
                    typed_parts.append(CanonicalDataPart(data=data))
                    output_parts.append(
                        json.dumps(data, ensure_ascii=False, default=str)
                    )
        artifacts = [
            await self._artifact_descriptor(
                room_id=room_id,
                artifact_ref=artifact_ref,
            )
            for artifact_ref in result.artifact_refs
        ]
        return CanonicalAgentCallDetail(
            run_id=run.run_id,
            public_call_id=public_call_id,
            status=result.status,
            output="\n".join(output_parts),
            parts=typed_parts,
            artifacts=artifacts,
        )


__all__ = [
    "CanonicalAgentCallDetail",
    "CanonicalAgentCallDetailService",
    "CanonicalAgentCallPart",
    "CanonicalArtifactDescriptor",
    "CanonicalDataPart",
    "CanonicalTextPart",
]
