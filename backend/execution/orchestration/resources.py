from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from common.utils.a2a_file_modes import mime_type_is_accepted
from models.room import UserAttachment


ResourceKind = Literal["attachment", "context", "artifact"]
ResourceOrigin = Literal["user_message", "context_projection", "agent_message", "system"]
ResourceStatus = Literal["ready", "processing", "failed", "unavailable"]


class ResourceProjectionRef(BaseModel):
    ref_id: str
    kind: Literal["context", "artifact"]
    source_ref_id: str
    mime_type: str
    status: ResourceStatus
    recommended_for_input_modes: list[str] = Field(default_factory=list)
    summary: str | None = None
    failure_reason: str | None = None


class ResourceRef(BaseModel):
    ref_id: str
    kind: ResourceKind
    origin: ResourceOrigin
    source_message_id: str | None = None
    source_agent_id: str | None = None
    file_name: str | None = None
    mime_type: str | None = None
    status: ResourceStatus
    summary: str | None = None
    token_estimate: int | None = None
    supported_by_agent_ids: list[str] = Field(default_factory=list)
    projections: list[ResourceProjectionRef] = Field(default_factory=list)


class ResourcePayload(BaseModel):
    ref_id: str
    kind: ResourceKind
    mime_type: str | None = None
    text: str | None = None
    summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AttachmentProjectionServicePort(Protocol):
    async def ensure_projection(
        self,
        attachment: UserAttachment,
        *,
        target_mime: str = "text/plain",
    ) -> tuple[ResourceProjectionRef, ResourcePayload | None]:
        raise NotImplementedError


def attachment_resource_ref_id(file_id: str) -> str:
    return f"file:{file_id}"


def text_projection_ref_id(file_id: str) -> str:
    return f"ctx:file-{file_id}:text"


def _attachment_summary(attachment: UserAttachment) -> str:
    return (
        f"{attachment.file_name} ({attachment.mime_type}, "
        f"{attachment.size_bytes} bytes)"
    )


def _agent_id(candidate: Any) -> str | None:
    value = getattr(candidate, "agent_id", None)
    return value if isinstance(value, str) and value else None


def _input_modes(candidate: Any) -> list[str]:
    raw = getattr(candidate, "input_modes", None)
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        return ["text"]
    modes = [str(item) for item in raw if str(item)]
    return modes or ["text"]


class OrchestrationResourceProvider:
    def __init__(
        self,
        *,
        projection_service: AttachmentProjectionServicePort | None = None,
    ) -> None:
        self._projection_service = projection_service
        self._payloads_by_ref: dict[str, ResourcePayload] = {}
        self._projection_refs_by_source: dict[str, ResourceProjectionRef] = {}

    async def list_resources(
        self,
        *,
        run_id: str,
        room_id: str,
        user_message_id: str,
        attachments: Sequence[UserAttachment],
        candidate_agents: Sequence[Any],
    ) -> list[ResourceRef]:
        resources: list[ResourceRef] = []
        for attachment in attachments:
            source_ref_id = attachment_resource_ref_id(attachment.file_id)
            supported_by_agent_ids = [
                agent_id
                for candidate in candidate_agents
                for agent_id in [_agent_id(candidate)]
                if agent_id is not None
                and mime_type_is_accepted(attachment.mime_type, _input_modes(candidate))
            ]
            projection = self._projection_refs_by_source.get(source_ref_id)
            if projection is None and attachment.mime_type == "application/pdf":
                projection = ResourceProjectionRef(
                    ref_id=text_projection_ref_id(attachment.file_id),
                    kind="context",
                    source_ref_id=source_ref_id,
                    mime_type="text/plain",
                    status="unavailable",
                    recommended_for_input_modes=["text"],
                    summary="Text projection has not been generated.",
                )
            resources.append(
                ResourceRef(
                    ref_id=source_ref_id,
                    kind="attachment",
                    origin="user_message",
                    source_message_id=user_message_id,
                    file_name=attachment.file_name,
                    mime_type=attachment.mime_type,
                    status="ready",
                    summary=_attachment_summary(attachment),
                    supported_by_agent_ids=supported_by_agent_ids,
                    projections=[projection] if projection is not None else [],
                )
            )
        return resources

    async def ensure_projection(
        self,
        ref_id: str,
        *,
        attachments: Sequence[UserAttachment],
        target_mime: str = "text/plain",
    ) -> ResourceProjectionRef:
        source = self._attachment_by_resource_ref(ref_id, attachments)
        if source is None:
            return ResourceProjectionRef(
                ref_id=f"{ref_id}:projection-missing",
                kind="context",
                source_ref_id=ref_id,
                mime_type=target_mime,
                status="failed",
                recommended_for_input_modes=["text"],
                summary="Attachment not found for projection.",
                failure_reason="attachment_ref_not_found",
            )
        if self._projection_service is None:
            projection = ResourceProjectionRef(
                ref_id=text_projection_ref_id(source.file_id),
                kind="context",
                source_ref_id=ref_id,
                mime_type=target_mime,
                status="unavailable",
                recommended_for_input_modes=["text"],
                summary="Text projection service is unavailable.",
            )
            self._projection_refs_by_source[ref_id] = projection
            return projection
        projection, payload = await self._projection_service.ensure_projection(
            source,
            target_mime=target_mime,
        )
        self._projection_refs_by_source[ref_id] = projection
        if payload is not None:
            self._payloads_by_ref[payload.ref_id] = payload
        return projection

    async def resolve_ref(
        self,
        ref_id: str,
        *,
        attachments: Sequence[UserAttachment],
    ) -> ResourcePayload | None:
        payload = self._payloads_by_ref.get(ref_id)
        if payload is not None:
            return payload
        attachment = self._attachment_by_resource_ref(ref_id, attachments)
        if attachment is None:
            return None
        return ResourcePayload(
            ref_id=ref_id,
            kind="attachment",
            mime_type=attachment.mime_type,
            text=None,
            summary=_attachment_summary(attachment),
            metadata={
                "file_id": attachment.file_id,
                "file_name": attachment.file_name,
                "s3_key": attachment.s3_key,
                "size_bytes": attachment.size_bytes,
            },
        )

    @staticmethod
    def _attachment_by_resource_ref(
        ref_id: str,
        attachments: Sequence[UserAttachment],
    ) -> UserAttachment | None:
        for attachment in attachments:
            if attachment_resource_ref_id(attachment.file_id) == ref_id:
                return attachment
        return None
