from datetime import datetime

from pydantic import BaseModel, Field

from common.file_upload_constants import (  # noqa: F401 - legacy model re-export
    MAX_INLINE_CONVERSIONS_PER_MESSAGE,
)
from common.utils.time import utcnow

IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
AUDIO_MIME_TYPES = {"audio/wav", "audio/mpeg", "audio/mp4", "audio/webm"}
VIDEO_MIME_TYPES = {"video/mp4", "video/webm"}
DOCUMENT_MIME_TYPES = {
    "text/plain",
    "text/markdown",
    "text/html",
    "text/csv",
    "application/json",
    "application/xml",
    "application/pdf",
    "application/zip",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
ALLOWED_MIME_TYPES = (
    IMAGE_MIME_TYPES | AUDIO_MIME_TYPES | VIDEO_MIME_TYPES | DOCUMENT_MIME_TYPES
)

MAX_ATTACHMENTS_PER_MESSAGE = 10
MAX_ATTACHMENT_REFS_PER_REQUEST = 50  # DoS guard on raw (pre-dedup) ref count


class FileUploadMetadata(BaseModel):
    """Stored in MongoDB `room_files` collection."""

    file_id: str
    room_id: str
    owner_id: str
    source: str = "user_upload"
    mime_type: str
    file_name: str
    size_bytes: int
    sha256: str
    status: str = "ready"
    version: int = 1
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class FileUploadResponse(BaseModel):
    """Returned to frontend after successful upload."""

    file_id: str
    file_url: str  # stable authenticated same-origin content URL
    mime_type: str
    file_name: str
    size_bytes: int
