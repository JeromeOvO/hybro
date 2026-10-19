"""PDF attachment text projection for agents that accept text but not PDF.

The orchestrator's resource tools ask this service for a bounded plain-text
projection of a user's PDF attachment.
"""

from __future__ import annotations

import hashlib
import json
from io import BytesIO
from typing import Any, Literal

from pydantic import BaseModel, Field
from pypdf import PdfReader

from models.room import UserAttachment

VOLATILE_KEYS = {
    "artifact_key",
    "source_agent_message_id",
    "source_agent_id",
    "message_id",
    "task_id",
    "context_id",
    "created_at",
    "updated_at",
}


def _stable_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _stable_value(item)
            for key, item in sorted(value.items())
            if key not in VOLATILE_KEYS
        }
    if isinstance(value, list):
        return [_stable_value(item) for item in value]
    if isinstance(value, str):
        return " ".join(value.split())
    return value


def canonical_content_fingerprint(value: Any) -> str:
    payload = json.dumps(
        _stable_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


ResourceKind = Literal["attachment", "context", "artifact"]
ResourceStatus = Literal["ready", "processing", "failed", "unavailable"]


class ResourceProjectionRef(BaseModel):
    ref_id: str
    kind: Literal["context", "artifact"]
    source_ref_id: str
    mime_type: str
    status: ResourceStatus
    content_fingerprint: str | None = None
    recommended_for_input_modes: list[str] = Field(default_factory=list)
    summary: str | None = None
    failure_reason: str | None = None


class ResourcePayload(BaseModel):
    ref_id: str
    kind: ResourceKind
    mime_type: str | None = None
    text: str | None = None
    content_fingerprint: str | None = None
    summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


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
                attachment.file_id,
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
            content_fingerprint=canonical_content_fingerprint(bounded_text),
            recommended_for_input_modes=["text"],
            summary=summary,
        )
        payload = ResourcePayload(
            ref_id=projection_ref_id,
            kind="context",
            mime_type="text/plain",
            text=bounded_text,
            content_fingerprint=canonical_content_fingerprint(bounded_text),
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


__all__ = [
    "AttachmentProjectionService",
    "ResourcePayload",
    "ResourceProjectionRef",
    "attachment_resource_ref_id",
    "canonical_content_fingerprint",
    "text_projection_ref_id",
]
