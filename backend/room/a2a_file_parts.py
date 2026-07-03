from __future__ import annotations

import base64
import logging
import math
from dataclasses import dataclass
from enum import Enum
from typing import Literal, Protocol

from common.errors import ObjectStorageError
from common.protocols import AttachmentContentReader
from common.types import FileContent, FilePart, Part
from common.utils.a2a_file_modes import agent_input_modes, mime_type_is_accepted
from models.room import UserAttachment

logger = logging.getLogger(__name__)


class A2AFileTransferMode(str, Enum):
    INLINE_BYTES = "inline_bytes"
    URI_REFERENCE = "uri_reference"


@dataclass(frozen=True)
class A2AOutboundFile:
    name: str
    mime_type: str
    storage_key: str
    size_bytes: int


AttachmentFailureCode = Literal[
    "agent_does_not_accept_file_type",
    "agent_card_unavailable",
    "file_too_large",
    "message_too_large",
    "file_unavailable",
    "storage_unavailable",
    "empty_file",
    "encoding_failed",
]


@dataclass(frozen=True)
class AttachmentPreflightFailure:
    code: AttachmentFailureCode
    message: str
    file_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class AttachmentFilePartsResult:
    parts: list[Part]
    failure: AttachmentPreflightFailure | None = None


@dataclass(frozen=True)
class AttachmentDispatchContext:
    room_id: str | None = None
    message_id: str | None = None
    agent_id: str | None = None


class AttachmentUriResolver(Protocol):
    async def get_uri(self, key: str, *, filename: str | None = None) -> str: ...


def encoded_base64_size(raw_size: int) -> int:
    if raw_size <= 0:
        return 0
    return 4 * math.ceil(raw_size / 3)


def _outbound_file_from_attachment(attachment: UserAttachment) -> A2AOutboundFile:
    return A2AOutboundFile(
        name=attachment.file_name,
        mime_type=attachment.mime_type or "application/octet-stream",
        storage_key=attachment.s3_key,
        size_bytes=attachment.size_bytes,
    )


def _failure(
    code: AttachmentFailureCode,
    message: str,
    *,
    file_names: tuple[str, ...] = (),
) -> AttachmentFilePartsResult:
    return AttachmentFilePartsResult(
        parts=[],
        failure=AttachmentPreflightFailure(
            code=code,
            message=message,
            file_names=file_names,
        ),
    )


def _preflight_failure(
    code: AttachmentFailureCode,
    message: str,
    *,
    file_names: tuple[str, ...] = (),
) -> AttachmentPreflightFailure:
    return AttachmentPreflightFailure(
        code=code,
        message=message,
        file_names=file_names,
    )


def _object_storage_error_exceeds_max_bytes(exc: ObjectStorageError) -> bool:
    details = exc.details or {}
    content_length = details.get("content_length")
    max_bytes = details.get("max_bytes")
    if content_length is not None and max_bytes is not None:
        try:
            if int(content_length) > int(max_bytes):
                return True
        except (TypeError, ValueError):
            pass
    return "exceeds max_bytes" in str(exc).lower()


async def build_inline_file_part(
    file: A2AOutboundFile,
    *,
    content_reader: AttachmentContentReader,
    max_raw_bytes: int,
    context: AttachmentDispatchContext | None = None,
) -> Part | AttachmentPreflightFailure:
    context = context or AttachmentDispatchContext()
    try:
        raw = await content_reader.get_bytes(file.storage_key, max_bytes=max_raw_bytes)
    except ObjectStorageError as exc:
        if _object_storage_error_exceeds_max_bytes(exc):
            return _preflight_failure(
                "file_too_large",
                f"Uploaded file '{file.name}' exceeds the maximum raw file size.",
                file_names=(file.name,),
            )
        logger.exception(
            "Attachment storage read failed during A2A inline dispatch",
            extra={
                "room_id": context.room_id,
                "message_id": context.message_id,
                "agent_id": context.agent_id,
                "storage_key": file.storage_key,
                "file_name": file.name,
                "mime_type": file.mime_type,
                "declared_size_bytes": file.size_bytes,
                "max_raw_bytes": max_raw_bytes,
            },
        )
        return _preflight_failure(
            "storage_unavailable",
            f"Uploaded file '{file.name}' is temporarily unavailable.",
            file_names=(file.name,),
        )
    except Exception:
        logger.exception(
            "Attachment storage read failed during A2A inline dispatch",
            extra={
                "room_id": context.room_id,
                "message_id": context.message_id,
                "agent_id": context.agent_id,
                "storage_key": file.storage_key,
                "file_name": file.name,
                "mime_type": file.mime_type,
                "declared_size_bytes": file.size_bytes,
                "max_raw_bytes": max_raw_bytes,
            },
        )
        return _preflight_failure(
            "storage_unavailable",
            f"Uploaded file '{file.name}' is temporarily unavailable.",
            file_names=(file.name,),
        )

    if raw is None:
        return _preflight_failure(
            "file_unavailable",
            f"Uploaded file '{file.name}' is unavailable.",
            file_names=(file.name,),
        )
    if len(raw) == 0:
        return _preflight_failure(
            "empty_file",
            f"Uploaded file '{file.name}' is empty.",
            file_names=(file.name,),
        )
    if len(raw) > max_raw_bytes:
        return _preflight_failure(
            "file_too_large",
            f"Uploaded file '{file.name}' exceeds the maximum raw file size.",
            file_names=(file.name,),
        )

    try:
        encoded = base64.b64encode(raw).decode("ascii")
    except Exception:
        logger.exception(
            "Attachment encoding failed during A2A inline dispatch",
            extra={
                "room_id": context.room_id,
                "message_id": context.message_id,
                "agent_id": context.agent_id,
                "storage_key": file.storage_key,
                "file_name": file.name,
                "mime_type": file.mime_type,
                "declared_size_bytes": file.size_bytes,
                "max_raw_bytes": max_raw_bytes,
            },
        )
        return _preflight_failure(
            "encoding_failed",
            f"Uploaded file '{file.name}' could not be encoded.",
            file_names=(file.name,),
        )

    return Part(
        root=FilePart(
            file=FileContent(
                bytes=encoded,
                mimeType=file.mime_type,
                name=file.name,
            )
        )
    )


async def build_uri_file_part(
    file: A2AOutboundFile,
    *,
    uri_resolver: AttachmentUriResolver,
) -> Part:
    uri = await uri_resolver.get_uri(file.storage_key, filename=file.name)
    return Part(
        root=FilePart(
            file=FileContent(
                uri=uri,
                mimeType=file.mime_type,
                name=file.name,
            )
        )
    )


async def build_attachment_file_parts(
    *,
    attachments: list[UserAttachment] | None,
    agent_card,
    content_reader: AttachmentContentReader,
    max_raw_bytes: int,
    max_encoded_bytes: int,
    context: AttachmentDispatchContext | None = None,
) -> AttachmentFilePartsResult:
    files = [
        _outbound_file_from_attachment(attachment)
        for attachment in (attachments or [])
    ]
    if not files:
        return AttachmentFilePartsResult(parts=[])

    modes = agent_input_modes(agent_card)
    unsupported = [
        file for file in files if not mime_type_is_accepted(file.mime_type, modes)
    ]
    if unsupported:
        details = ", ".join(
            f"{file.name} ({file.mime_type})" for file in unsupported
        )
        return _failure(
            "agent_does_not_accept_file_type",
            f"Agent does not accept the uploaded file type for: {details}.",
            file_names=tuple(file.name for file in unsupported),
        )

    oversize_files = [file for file in files if file.size_bytes > max_raw_bytes]
    if oversize_files:
        details = ", ".join(file.name for file in oversize_files)
        return _failure(
            "file_too_large",
            f"Uploaded file exceeds the maximum raw file size: {details}.",
            file_names=tuple(file.name for file in oversize_files),
        )

    aggregate_encoded_size = sum(
        encoded_base64_size(file.size_bytes) for file in files
    )
    if aggregate_encoded_size > max_encoded_bytes:
        return _failure(
            "message_too_large",
            (
                "Uploaded files exceed the aggregate encoded message size limit "
                f"({aggregate_encoded_size} > {max_encoded_bytes})."
            ),
            file_names=tuple(file.name for file in files),
        )

    parts: list[Part] = []
    for file in files:
        part = await build_inline_file_part(
            file,
            content_reader=content_reader,
            max_raw_bytes=max_raw_bytes,
            context=context,
        )
        if isinstance(part, AttachmentPreflightFailure):
            return AttachmentFilePartsResult(parts=[], failure=part)
        parts.append(part)
    return AttachmentFilePartsResult(parts=parts)
