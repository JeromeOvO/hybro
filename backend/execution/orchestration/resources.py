from __future__ import annotations

from collections import OrderedDict
from collections.abc import Sequence
from io import BytesIO
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field
from pypdf import PdfReader

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


class AttachmentProjectionService:
    def __init__(
        self,
        *,
        content_reader: Any,
        max_pdf_bytes: int = 10 * 1024 * 1024,
        max_text_chars: int = 120_000,
    ) -> None:
        self._content_reader = content_reader
        self._max_pdf_bytes = max_pdf_bytes
        self._max_text_chars = max_text_chars

    async def ensure_projection(
        self,
        attachment: UserAttachment,
        *,
        target_mime: str = "text/plain",
    ) -> tuple[ResourceProjectionRef, ResourcePayload | None]:
        source_ref_id = attachment_resource_ref_id(attachment.file_id)
        projection_ref_id = text_projection_ref_id(attachment.file_id)
        if target_mime != "text/plain":
            return self._failed_projection(
                projection_ref_id,
                source_ref_id,
                target_mime,
                "unsupported_projection_mime",
            ), None
        if attachment.mime_type != "application/pdf":
            return self._failed_projection(
                projection_ref_id,
                source_ref_id,
                target_mime,
                "unsupported_projection_source_mime",
            ), None
        if attachment.size_bytes > self._max_pdf_bytes:
            return self._failed_projection(
                projection_ref_id,
                source_ref_id,
                target_mime,
                "pdf_projection_too_large",
            ), None

        try:
            data = await self._content_reader.get_bytes(
                attachment.s3_key,
                max_bytes=self._max_pdf_bytes,
            )
            if not data:
                return self._failed_projection(
                    projection_ref_id,
                    source_ref_id,
                    target_mime,
                    "pdf_extract_failed",
                ), None
            reader = PdfReader(BytesIO(data))
            if reader.is_encrypted:
                return self._failed_projection(
                    projection_ref_id,
                    source_ref_id,
                    target_mime,
                    "pdf_encrypted",
                ), None
            page_text: list[str] = []
            for page in reader.pages:
                extracted = page.extract_text() or ""
                if extracted.strip():
                    page_text.append(extracted.strip())
            text = "\n\n".join(page_text).strip()
        except Exception:
            return self._failed_projection(
                projection_ref_id,
                source_ref_id,
                target_mime,
                "pdf_extract_failed",
            ), None

        if not text:
            return self._failed_projection(
                projection_ref_id,
                source_ref_id,
                target_mime,
                "pdf_text_empty",
            ), None

        is_truncated = len(text) > self._max_text_chars
        bounded_text = text[: self._max_text_chars] if is_truncated else text
        summary = (
            f"Extracted {len(bounded_text)} characters from "
            f"{len(reader.pages)} PDF page(s)."
        )
        projection = ResourceProjectionRef(
            ref_id=projection_ref_id,
            kind="context",
            source_ref_id=source_ref_id,
            mime_type="text/plain",
            status="ready",
            recommended_for_input_modes=["text"],
            summary=summary,
        )
        payload = ResourcePayload(
            ref_id=projection_ref_id,
            kind="context",
            mime_type="text/plain",
            text=bounded_text,
            summary=summary,
            metadata={
                "source_ref_id": source_ref_id,
                "file_id": attachment.file_id,
                "file_name": attachment.file_name,
                "char_count": len(text),
                "page_count": len(reader.pages),
                "is_truncated": is_truncated,
            },
        )
        return projection, payload

    @staticmethod
    def _failed_projection(
        ref_id: str,
        source_ref_id: str,
        target_mime: str,
        reason: str,
    ) -> ResourceProjectionRef:
        return ResourceProjectionRef(
            ref_id=ref_id,
            kind="context",
            source_ref_id=source_ref_id,
            mime_type=target_mime,
            status="failed",
            recommended_for_input_modes=["text"],
            summary=f"Projection failed: {reason}",
            failure_reason=reason,
        )


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


def _candidate_needs_text_projection(candidate: Any, source_mime: str) -> bool:
    modes = _input_modes(candidate)
    normalized_modes = {mode.strip().lower() for mode in modes}
    accepts_text = "text" in normalized_modes or mime_type_is_accepted(
        "text/plain", modes
    )
    return accepts_text and not mime_type_is_accepted(source_mime, modes)


class OrchestrationResourceProvider:
    def __init__(
        self,
        *,
        projection_service: AttachmentProjectionServicePort | None = None,
        max_cached_runs: int = 128,
    ) -> None:
        self._projection_service = projection_service
        self._max_cached_runs = max_cached_runs
        self._payloads_by_run: OrderedDict[str, dict[str, ResourcePayload]] = (
            OrderedDict()
        )
        self._projection_refs_by_run: OrderedDict[
            str, dict[str, ResourceProjectionRef]
        ] = OrderedDict()

    async def list_resources(
        self,
        *,
        run_id: str,
        room_id: str,
        user_message_id: str,
        attachments: Sequence[UserAttachment],
        candidate_agents: Sequence[Any],
    ) -> list[ResourceRef]:
        self._touch_run(run_id)
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
            projection = self._projection_refs_by_run[run_id].get(source_ref_id)
            needs_text_projection = any(
                _candidate_needs_text_projection(candidate, attachment.mime_type)
                for candidate in candidate_agents
            )
            if (
                projection is None
                and attachment.mime_type == "application/pdf"
                and needs_text_projection
            ):
                projection = await self.ensure_projection(
                    source_ref_id,
                    run_id=run_id,
                    attachments=attachments,
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
        run_id: str,
        attachments: Sequence[UserAttachment],
        target_mime: str = "text/plain",
    ) -> ResourceProjectionRef:
        self._touch_run(run_id)
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
            self._projection_refs_by_run[run_id][ref_id] = projection
            return projection
        projection, payload = await self._projection_service.ensure_projection(
            source,
            target_mime=target_mime,
        )
        self._projection_refs_by_run[run_id][ref_id] = projection
        if payload is not None:
            self._payloads_by_run[run_id][payload.ref_id] = payload
        return projection

    async def resolve_ref(
        self,
        ref_id: str,
        *,
        run_id: str,
        attachments: Sequence[UserAttachment],
    ) -> ResourcePayload | None:
        self._touch_run(run_id)
        payload = self._payloads_by_run[run_id].get(ref_id)
        if payload is not None:
            return payload
        projection_source = self._attachment_by_projection_ref(ref_id, attachments)
        if projection_source is not None:
            projection = await self.ensure_projection(
                attachment_resource_ref_id(projection_source.file_id),
                run_id=run_id,
                attachments=attachments,
            )
            if projection.status != "ready":
                return None
            return self._payloads_by_run[run_id].get(ref_id)
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
                "size_bytes": attachment.size_bytes,
            },
        )

    def _touch_run(self, run_id: str) -> None:
        if run_id in self._payloads_by_run:
            self._payloads_by_run.move_to_end(run_id)
            self._projection_refs_by_run.move_to_end(run_id)
            return
        self._payloads_by_run[run_id] = {}
        self._projection_refs_by_run[run_id] = {}
        while len(self._payloads_by_run) > self._max_cached_runs:
            evicted_run_id, _ = self._payloads_by_run.popitem(last=False)
            self._projection_refs_by_run.pop(evicted_run_id, None)

    @staticmethod
    def _attachment_by_resource_ref(
        ref_id: str,
        attachments: Sequence[UserAttachment],
    ) -> UserAttachment | None:
        for attachment in attachments:
            if attachment_resource_ref_id(attachment.file_id) == ref_id:
                return attachment
        return None

    @staticmethod
    def _attachment_by_projection_ref(
        ref_id: str,
        attachments: Sequence[UserAttachment],
    ) -> UserAttachment | None:
        for attachment in attachments:
            if text_projection_ref_id(attachment.file_id) == ref_id:
                return attachment
        return None
