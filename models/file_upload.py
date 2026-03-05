from datetime import datetime

from pydantic import BaseModel, Field

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
ALLOWED_MIME_TYPES = IMAGE_MIME_TYPES | AUDIO_MIME_TYPES | VIDEO_MIME_TYPES | DOCUMENT_MIME_TYPES

MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # Deprecated: runtime limit is settings.max_file_size_mb
MAX_ATTACHMENTS_PER_MESSAGE = 10
MAX_ATTACHMENT_REFS_PER_REQUEST = 50  # DoS guard on raw (pre-dedup) ref count
MAX_INLINE_CONVERSIONS_PER_MESSAGE = 20


class FileUploadMetadata(BaseModel):
    """Stored in MongoDB `file_uploads` collection."""

    file_id: str
    room_id: str
    user_id: str
    s3_key: str
    mime_type: str
    file_name: str
    size_bytes: int
    uploaded_at: datetime = Field(default_factory=utcnow)


class FileUploadResponse(BaseModel):
    """Returned to frontend after successful upload."""

    file_id: str
    file_url: str  # presigned URL (ephemeral -- do NOT persist this)
    mime_type: str
    file_name: str
    size_bytes: int
