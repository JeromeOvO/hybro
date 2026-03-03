# Backend Multimodal Support Design

**Status**: Proposal
**Depends on**: None (phases are independently shippable)
**Relationship to other docs**:
- `MULTIMODAL_SUPPORT_DESIGN.md` (frontend) -- Phases 1-4 frontend counterpart
- `CONTEXT_MEMORY_SYSTEM_DESIGN.md` section 6.8 -- S3 expansion placeholder
- `HITL_DESIGN.md` -- HITL replies remain text-only until both HITL and file input are stable

---

## 1. Problem Statement

The Hybro backend is **text-only** today. Every layer -- from message ingestion to A2A
dispatch to SSE broadcast to memory storage -- treats content as plain strings. However:

1. **A2A agents already declare multi-modal capabilities.** Agent cards include
   `defaultOutputModes: ["text", "image"]` and `defaultInputModes: ["text", "file"]`,
   but the backend hardcodes `acceptedOutputModes=["text/plain"]` in 4 places
   (`a2a_service.py` lines 319, 518, 571, 635), actively blocking non-text agent output.

2. **The A2A SDK defines `FilePart`, `DataPart`, and `TextPart`**, all discriminated by
   `kind`. The backend imports all three (`common/types.py` line 33:
   `Part = Annotated[TextPart | FilePart | DataPart, ...]`) but only ever instantiates
   `TextPart` across 22+ call sites in 10 production files.

3. **Users cannot send images or files.** `RoomCenterUserMessageRequest`
   (`models/request.py` lines 232-241) only accepts `user_input: str`. There is no
   file upload endpoint, no `multipart/form-data` handling, and no attachment model.

4. **Non-text agent output is silently discarded.** Both `get_text_from_message()`
   and `extract_text_from_artifacts()` (`common/utils/a2a_helpers.py` lines 73-108)
   only read `.text` fields from parts, ignoring `FilePart` and `DataPart` entirely.

5. **No binary storage backend.** `StorageType.S3` exists in the compaction model
   (`models/compaction.py` line 31) but `content_storage_service.py` line 228 raises
   `NotImplementedError("S3 expansion not yet implemented")`.

This design adds multi-modal support across 4 independently shippable phases.

---

## 2. Current State Audit

| Layer | Current | Multi-modal gap |
|-------|---------|-----------------|
| A2A send config | `acceptedOutputModes=["text/plain"]` hardcoded in 4 places | Blocks agents from returning images/files |
| User message input | `user_input: str` (JSON body) | No file upload, no multipart/form-data |
| Message storage | `MessageContent.message_text: str` + `message_task: Task` | No fields for file refs, binary metadata |
| Artifact handling | `extract_text_from_artifacts()` reads only `.text` | `FilePart`/`DataPart` silently dropped |
| SSE events | `content: str` in `agent_response`, `agent_token` | No structured parts in content events |
| SSE `artifact_update` | Passes raw artifact object through | **Partially works** -- if agent returned `FilePart` it would reach frontend |
| Binary storage | S3 designed in `CONTEXT_MEMORY_SYSTEM_DESIGN.md` section 6.8 but raises `NotImplementedError` | No binary storage backend |
| Memory/context | `ContentType` enum has `TEXT`, `TOOL_RESULT`, `AGENT_RESPONSE`; placeholders for `IMAGE`, `FILE`, `VIDEO`, `AUDIO` | Only text compaction implemented |
| Part types | `Part = TextPart / FilePart / DataPart` (from `a2a.types`) | `FilePart`/`DataPart` never instantiated |
| Text extraction | `get_text_from_message()` uses `part.root.text`; `extract_text_from_artifacts()` uses two-level check | Inconsistent logic, both discard non-text |
| Context assembly | `ConversationTurn.content: str` only | No attachment metadata in context window |

### What Already Works (Partially)

1. **Artifact SSE passthrough**: `ResponseProcessor._handle_stream_artifact_update()`
   forwards the raw artifact object via SSE, including any `FilePart`/`DataPart`.

2. **A2A SDK types are available**: `Part`, `TextPart`, `FilePart`, `DataPart`
   are imported from `a2a.types`. The SDK also provides `FileWithUri` and
   `FileWithBytes` for constructing `FilePart` objects. Note: `FileContent` in
   `common/types.py` is a **local** class (not from the SDK) -- new code should
   use the SDK types directly.

3. **Agent cards declare modalities**: `default_output_modes` and
   `default_input_modes` are stored on agent cards. Note: the SDK `AgentCard`
   (from `a2a.types`) uses snake_case field names; the local `AgentCard`
   (in `common/types.py`) uses camelCase. Code must use the correct field name
   for whichever type is imported.

4. **Compaction model has S3 fields**: `ContentReference` already has `s3_bucket`,
   `s3_key`, `mime_type`, `size_bytes` fields.

---

## 3. Phased Design Overview

```
Phase 1: S3 File Storage Layer           (backend infrastructure)
    |
Phase 2: Message Model + Attachments     (data model, message flow)
    |
Phase 3: A2A Multimodal Negotiation      (agent I/O, part extraction)
    |
Phase 4: Tests                           (comprehensive test coverage)
```

Each phase is independently shippable. Phase 1 has no user-visible impact (infra only).
Phase 2 enables user file input. Phase 3 unlocks multi-modal agent I/O. Phase 4 locks
down quality.

### Bidirectional Multimodal Data Flow

```
USER → AGENT (file input):

  Frontend          Backend                              Agent
  ────────          ───────                              ─────
  File picker  →  POST /files/upload  →  S3 (store)
                  ← file_id, presigned URL
  Send message  →  POST /sendMessage
  (with            + attachments[]
   attachments)    → resolve s3_key from file_uploads
                   → build_turn_content() (text annotation)
                   → _build_message_parts()
                     → TextPart + FilePart(uri=presigned_url)
                   → a2a_service.send_message_streaming()  →  Agent receives
                     (accepted_output_modes negotiated)         FilePart with URI

AGENT → USER (multimodal output):

  Agent             Backend                              Frontend
  ─────             ───────                              ────────
  Returns     →  ResponseProcessor handles:
  message/         ├─ message event:
  artifact           │  extract_parts() → text + file + data
  with               │  base64 → S3 if inline bytes
  FilePart/          │  SSE agent_token (text)
  DataPart           │  SSE agent_response (text + parts)
                     ├─ artifact-update event:
                     │  _convert_inline_bytes_to_s3()
                     │  SSE artifact_update (raw artifact)  →  Frontend renders
                     └─ sync response:                          PartRenderer
                        extract_parts() → text + parts            (image/file/
                        SSE agent_response + task_update           json display)

STORAGE:

  User uploads  →  S3: uploads/{room_id}/{file_id}/{filename}
  Agent base64  →  S3: artifacts/{room_id}/{message_id}/{index}.{ext}
  Metadata      →  MongoDB: file_uploads collection
  Attachments   →  MongoDB: room_user_messages.message_content.attachments
  Agent output  →  MongoDB: room_agent_messages.message_content.message_task
                   (artifacts with FilePart/DataPart preserved in Task object)
```

---

## 4. Phase 1 -- S3 File Storage Layer

**Goal**: Build the infrastructure for storing and serving binary files via AWS S3.

**New files**: `services/s3_service.py`, `services/file_upload_service.py`,
`api/files.py`, `models/file_upload.py`

**Dependencies**: Add `aioboto3` to `pyproject.toml`

### 4.1 S3 Service (`services/s3_service.py`)

Async S3 wrapper using `aioboto3` to keep the event loop non-blocking (the backend
uses `motor` for async MongoDB and `httpx` for async HTTP -- `aioboto3` maintains
this pattern).

```python
from common.utils.logger import get_logger

logger = get_logger(__name__)


class S3Service:
    """Async S3 operations for file storage.

    Uses aioboto3 for non-blocking uploads and presigned URL generation.
    Shared by FileUploadService (user uploads) and ContentStorageService
    (compaction S3 expansion).
    """

    def __init__(self):
        self._session = aioboto3.Session()
        self._bucket = settings.s3_bucket_name
        self._region = settings.s3_region
        self._presigned_url_ttl = settings.s3_presigned_url_ttl
        self._url_cache: dict[str, tuple[str, float]] = {}
        self._cache_ttl = self._presigned_url_ttl / 2

    async def upload_file(
        self,
        file_data: BinaryIO | bytes,
        s3_key: str,
        content_type: str,
        content_length: int | None = None,
    ) -> str:
        """Upload file to S3.

        Accepts io.BytesIO or raw bytes. For files under MAX_FILE_SIZE_MB,
        full buffering is acceptable (content already read for magic-byte
        validation). Returns the s3_key for storage in MongoDB.
        """

    async def generate_presigned_url(self, s3_key: str) -> str:
        """Generate a presigned GET URL with in-memory caching.

        Cache strategy: store (url, expiry_time) keyed by s3_key.
        Cache TTL = half of presigned URL TTL (e.g., URL valid 1h, cache 30min).
        This avoids regenerating URLs on rapid page refreshes while ensuring
        URLs don't expire before they're used.
        """

    async def batch_presigned_urls(self, s3_keys: list[str]) -> dict[str, str]:
        """Generate presigned URLs for multiple keys (used by message retrieval).

        Returns {s3_key: presigned_url} dict. Uses cache where available.
        """

    async def delete_file(self, s3_key: str) -> bool:
        """Delete a file from S3. Returns True if deleted."""

    async def head_file(self, s3_key: str) -> dict | None:
        """Check if file exists and get metadata. Returns None if not found."""

    async def delete_prefix(self, prefix: str) -> int:
        """Delete all objects under an S3 prefix. Returns count deleted.

        Used by room deletion to clean up all uploads and artifacts for a room.
        """

    async def download_text(self, s3_key: str) -> str | None:
        """Download a text file from S3 and return its content as a string.

        Used by compaction expansion to retrieve full turn content.
        Returns None if the object doesn't exist.
        """
```

### 4.2 File Upload Models (`models/file_upload.py`)

```python
from pydantic import BaseModel, Field
from datetime import datetime
from common.utils.time import utcnow

IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
DOCUMENT_MIME_TYPES = {"application/pdf", "text/plain", "text/csv",
                       "application/json"}
ALLOWED_MIME_TYPES = IMAGE_MIME_TYPES | DOCUMENT_MIME_TYPES
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_ATTACHMENTS_PER_MESSAGE = 10
MAX_ATTACHMENT_REFS_PER_REQUEST = 50  # DoS guard on raw (pre-dedup) ref count


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
    file_url: str        # presigned URL (ephemeral -- do NOT persist this)
    mime_type: str
    file_name: str
    size_bytes: int
```

### 4.3 File Upload Service (`services/file_upload_service.py`)

Orchestrates validation, S3 upload, and MongoDB metadata write.

```python
from common.utils.logger import get_logger

logger = get_logger(__name__)


class FileUploadService:
    """Validates and stores user-uploaded files.

    Validation pipeline:
    1. MIME type check against ALLOWED_MIME_TYPES
    2. File size check against MAX_FILE_SIZE_BYTES
    3. Magic byte validation (actual content vs declared MIME)
    """

    def __init__(self):
        self._s3 = None  # lazy: resolved on first use to avoid circular imports

    @property
    def s3(self) -> S3Service:
        if self._s3 is None:
            from services.s3_service import s3_service
            self._s3 = s3_service
        return self._s3

    async def upload(
        self,
        file: UploadFile,
        room_id: str,
        user_id: str,
    ) -> FileUploadResponse:
        """Full upload pipeline: validate -> S3 upload -> MongoDB metadata.

        Raises:
            HTTPException(415): Invalid MIME type
            HTTPException(413): File too large
            HTTPException(422): Content-type mismatch (magic bytes)
        """
        # 1. Validate MIME type
        if file.content_type not in ALLOWED_MIME_TYPES:
            raise HTTPException(415, f"Unsupported file type: {file.content_type}")

        # 2. Read and validate size (streaming with size check)
        content = await file.read()
        if len(content) > MAX_FILE_SIZE_BYTES:
            raise HTTPException(413, f"File exceeds {MAX_FILE_SIZE_BYTES} bytes")

        # 3. Validate magic bytes match declared MIME
        actual_mime = self._detect_mime(content)
        if actual_mime and not self._mime_compatible(file.content_type, actual_mime):
            raise HTTPException(422, "File content doesn't match declared type")

        # 4. Upload to S3
        file_id = uuid4().hex
        s3_key = f"uploads/{room_id}/{file_id}/{file.filename}"
        await self.s3.upload_file(
            file_data=io.BytesIO(content),
            s3_key=s3_key,
            content_type=file.content_type,
            content_length=len(content),
        )

        # 5. Store metadata in MongoDB (compensating delete on failure)
        metadata = FileUploadMetadata(
            file_id=file_id,
            room_id=room_id,
            user_id=user_id,
            s3_key=s3_key,
            mime_type=file.content_type,
            file_name=file.filename or "unnamed",
            size_bytes=len(content),
        )
        try:
            await mongodb.file_uploads_collection.insert_one(metadata.model_dump())
        except Exception:
            logger.error("MongoDB insert failed after S3 upload, cleaning up S3 object: %s", s3_key)
            await self.s3.delete_file(s3_key)
            raise HTTPException(500, "Failed to store file metadata")

        # 6. Generate presigned URL for immediate use
        presigned_url = await self.s3.generate_presigned_url(s3_key)

        return FileUploadResponse(
            file_id=file_id,
            file_url=presigned_url,
            mime_type=file.content_type,
            file_name=file.filename or "unnamed",
            size_bytes=len(content),
        )

    # --- Private helpers ---

    MAGIC_BYTES = {
        b"\x89PNG": "image/png",
        b"\xff\xd8\xff": "image/jpeg",
        b"GIF87a": "image/gif",
        b"GIF89a": "image/gif",
        b"RIFF": "image/webp",  # RIFF....WEBP
        b"%PDF": "application/pdf",
    }

    def _detect_mime(self, content: bytes) -> str | None:
        """Detect MIME type from magic bytes. Returns None if unrecognized."""
        for magic, mime in self.MAGIC_BYTES.items():
            if content[:len(magic)] == magic:
                if mime == "image/webp" and content[8:12] != b"WEBP":
                    continue
                return mime
        return None

    @staticmethod
    def _mime_compatible(declared: str, detected: str) -> bool:
        """Check if declared MIME is compatible with detected.

        Allows minor variations (e.g., declared image/jpeg vs detected
        image/jpeg are obviously fine). Strict match on major type.
        """
        return declared.split("/")[0] == detected.split("/")[0]
```

### 4.4 File Upload Endpoint (`api/files.py`)

```python
from fastapi import APIRouter, Depends, UploadFile, Form
from common.auth import ClerkUser, get_current_user

router = APIRouter(prefix="/files", tags=["files"])

@router.post("/upload")
async def upload_file(
    file: UploadFile,
    room_id: str = Form(...),
    user: ClerkUser = Depends(get_current_user),
):
    """Upload a file to S3 for attachment to a room message.

    Accepts multipart/form-data with:
    - file: The file to upload
    - room_id: The room this file belongs to

    Returns FileUploadResponse with file_id and presigned URL.
    """
    # Verify user owns the room
    await verify_room_ownership(room_id, user)

    return await file_upload_service.upload(
        file=file,
        room_id=room_id,
        user_id=user.user_id,
    )
```

Register in `main.py`:
```python
from api.files import router as files_router
app.include_router(
    files_router,
    prefix=api_prefix,
    tags=["files"],
    dependencies=[Depends(get_current_user)],
)
```

### 4.5 Configuration

#### `.env.example` additions

```env
# AWS S3 Settings (for file uploads and binary content storage)
S3_BUCKET_NAME=hybro-uploads
S3_REGION=us-east-1
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
S3_PRESIGNED_URL_TTL=3600
MAX_FILE_SIZE_MB=10
```

#### `config/settings.py` additions

Add to the `Settings(BaseSettings)` class:

```python
# AWS S3
s3_bucket_name: str = ""
s3_region: str = "us-east-1"
aws_access_key_id: str = ""
aws_secret_access_key: str = ""
s3_presigned_url_ttl: int = 3600
max_file_size_mb: int = 10
```

These are auto-mapped from env vars by Pydantic `BaseSettings` (e.g., `S3_BUCKET_NAME` -> `s3_bucket_name`).

### 4.6 MongoDB Collection: `file_uploads`

```javascript
// Indexes
db.file_uploads.createIndex({ "file_id": 1 }, { unique: true })
db.file_uploads.createIndex({ "room_id": 1, "uploaded_at": -1 })
db.file_uploads.createIndex({ "user_id": 1, "uploaded_at": -1 })
```

Add property to `database/mongodb.py` (follows existing pattern for all collections):

```python
@property
def file_uploads_collection(self):
    """Get file_uploads collection"""
    if not self.client:
        raise ConnectionError(
            "MongoDB client is not connected. Please call connect() first."
        )
    return self.db.file_uploads
```

### 4.7 Service Instantiation (`main.py` lifespan)

Follow the existing codebase pattern of module-level singletons (matching
`sse_manager`, `content_storage_service`, `agent_health_service`, etc.).
The lifespan context manager calls `.start()` / `.connect()` on these singletons:

```python
# services/s3_service.py (module-level):
s3_service = S3Service()

# services/file_upload_service.py (module-level):
file_upload_service = FileUploadService()
```

The `S3Service` is shared -- it will also be imported into `ContentStorageService`
later to implement the S3 expansion for compaction (replacing the
`NotImplementedError` in `content_storage_service.py` line 228).

**S3Service injection into existing services**: Services that need S3 access
(`ResponseProcessor`, `ContentStorageService`, `room_services`) should use the
same lazy property pattern as `FileUploadService`:

```python
@property
def s3_service(self) -> S3Service:
    if self._s3_service is None:
        from services.s3_service import s3_service
        self._s3_service = s3_service
    return self._s3_service
```

Add `self._s3_service = None` to each service's `__init__`. This avoids
circular imports and follows the lazy-resolution pattern used by `HITLService`
for `_db_service`.

### 4.8 S3 Key Structure

```
uploads/
  {room_id}/
    {file_id}/
      {original_filename}     # user uploads (Phase 1)
artifacts/
  {room_id}/
    {message_id}/
      {part_index}.{ext}      # base64-to-S3 converted artifacts (Phase 3)
compaction/
  {room_id}/
    {turn_id}.txt             # compacted conversation content (future)
```

---

## 5. Phase 2 -- Message Model + Attachment Support

**Goal**: Extend the message data model so user messages can carry file attachments,
and ensure attachments flow through storage, retrieval, and context assembly.

**Files to modify**: `models/room.py`, `models/request.py`, `api/room_center.py`,
`services/room_services.py`, `services/sse_services.py`, `services/memory_service.py`,
`modules/RoomMessageCenter.py`

### 5.1 `UserAttachment` Model (`models/room.py`)

Add above `MessageContent`:

```python
class UserAttachment(BaseModel):
    """A file attached to a user message. Stored alongside message in MongoDB.

    file_url is ephemeral -- generated from s3_key at read time via presigned
    URL, never persisted.
    """
    file_id: str
    s3_key: str
    mime_type: str
    file_name: str
    size_bytes: int
    file_url: str | None = Field(default=None, json_schema_extra={"readOnly": True})
```

**DB write exclusion enforcement**: Pydantic v2's `model_dump(exclude={"field"})` is
per-call and can't cleanly reach nested fields in `UserAttachment` (nested inside
`MessageContent` inside `RoomUserMessage`). Two complementary strategies:

1. **Never set `file_url` before persistence** — the resolution code in section 5.5
   explicitly leaves `file_url` unset (`None` by default) when constructing
   `UserAttachment`. It is only assigned post-read (section 5.6).
2. **Defense-in-depth at DB write layer** — a thin helper strips `file_url` from the
   serialized dict before MongoDB write, catching any accidental leakage. Applied to
   **both** insert and update paths:

```python
# In database/mongodb.py:

def _strip_file_urls(doc: dict) -> None:
    """Remove file_url from serialized attachments to prevent persistence.

    Applied to both insert and update operations on user messages.
    Also handles $set-wrapped docs from update operations.
    """
    target = doc.get("$set", doc)
    content = target.get("message_content")
    if not content:
        return
    for att in content.get("attachments") or []:
        att.pop("file_url", None)

async def add_room_user_message(self, room_user_message: RoomUserMessage) -> str:
    doc = room_user_message.model_dump(mode="json")
    _strip_file_urls(doc)
    result = await self.room_user_messages_collection.insert_one(doc)
    return str(result.inserted_id)

async def update_room_user_message_by_message_id(
    self, message_id: str, room_user_message: RoomUserMessage
) -> bool:
    update_doc = {"$set": room_user_message.model_dump(exclude_unset=True, mode="json")}
    _strip_file_urls(update_doc)
    result = await self.room_user_messages_collection.update_one(
        {"message_id": message_id}, update_doc,
    )
    return result.modified_count > 0
```

This approach:
- Works with the existing `model_dump(mode="json")` pattern
- Does not affect API response serialization (FastAPI still sees `file_url`)
- Is explicit and auditable
- Handles the case where a future code path accidentally sets `file_url` before write

### 5.2 Extend `MessageContent` (`models/room.py` lines 51-54)

```python
# Before:
class MessageContent(BaseModel):
    message_text: str | None = None
    message_task: Task | None = None

# After:
class MessageContent(BaseModel):
    message_text: str | None = None
    message_task: Task | None = None
    attachments: list[UserAttachment] | None = None
    content_summary: dict | None = None
```

`content_summary` is populated at write time:
```python
{
    "has_images": True,
    "has_files": False,
    "attachment_count": 2,
    "mime_types": ["image/png", "image/jpeg"]
}
```

This enables efficient queries like "find all messages with images" without scanning
nested artifact structures.

**Backward compatibility**: Existing messages have `attachments=None` and
`content_summary=None`. Pydantic defaults handle this -- no migration needed.

### 5.3 Extend `RoomCenterUserMessageRequest` (`models/request.py` lines 232-241)

```python
class UserAttachmentRequest(BaseModel):
    """Wire format from frontend. Only file_id is used server-side; all metadata
    is resolved from the file_uploads collection to prevent spoofing.
    """
    file_id: str
    # file_url may be sent by frontend for optimistic rendering.
    # Server IGNORES this field — not read, stored, or forwarded.
    # Present so Pydantic doesn't reject the payload if frontend includes it.
    file_url: str | None = None


class RoomCenterUserMessageRequest(BaseModel):
    room_id: str | None = None
    message_id: str | None = None
    related_message_id: str | None = None
    user_id: str | None = None
    user_name: str | None = None
    user_input: str | None = None
    message_created_at: datetime | None = None
    extend_info: dict[str, Any] | None = None
    message: RoomUserMessage | None = None
    attachments: list[UserAttachmentRequest] | None = None  # NEW (top-level)
    inline_file_ids: list[str] | None = None  # NEW — extracted from raw dict at API layer
```

**Why no `max_length` on `attachments`**: The semantic limit (`MAX_ATTACHMENTS_PER_MESSAGE`)
is enforced after merge + dedup in `_resolve_attachments()` (section 5.5). A Pydantic-level
`max_length` on the top-level list alone would reject valid requests where duplicates dedup
below the limit, and wouldn't account for inline contributions. To guard against DoS from
oversized raw arrays, a separate `MAX_ATTACHMENT_REFS_PER_REQUEST` (50) check runs at the
API layer immediately after collecting both sources, before dedup (see section 5.4 pseudocode).

### 5.4 API Endpoint Handlers (`api/room_center.py`)

There are **two** user message entry points, both must handle attachments
identically:

1. `POST /roomCenter/sendMessage` (line 203) -- primary path; persists message
   then auto-triggers background processing.
2. `POST /roomCenter/createAndParseUserMessage` (line 164) -- legacy path;
   persists message then runs @mention parsing.

Both read `request.json()`, construct `RoomCenterUserMessageRequest`, and
delegate to `room_services`. The attachment extraction is identical:

```python
# Shared attachment extraction for BOTH endpoints:
request_data = await request.json()
room_id = request_data.get("room_id")
message = request_data.get("message")
attachments = request_data.get("attachments")  # NEW (top-level)

# Extract inline file_ids from raw dict BEFORE Pydantic model construction.
# MessageContent.attachments is typed as list[UserAttachment] (requires s3_key,
# mime_type, etc.), but the frontend only has file_id + file_url from the upload
# response — not s3_key. Deserializing into UserAttachment would fail.
# Solution: pluck file_ids from the raw dict, remove the field, pass them separately.
msg_content = (message if isinstance(message, dict) else {}).get("message_content")
msg_content = msg_content if isinstance(msg_content, dict) else {}
raw_inline_attachments = msg_content.pop("attachments", None)

# Pre-dedup DoS guard: check raw element counts BEFORE iterating.
# This ensures a 10k-element array is rejected without any per-item work.
# Returns RoomCenterUserMessageResponse (not HTTPException) to match the
# error contract used by _resolve_attachments() and all other attachment
# errors from this endpoint — keeps a single error shape for the frontend.
top_level_count = len(attachments) if isinstance(attachments, list) else 0
inline_count = len(raw_inline_attachments) if isinstance(raw_inline_attachments, list) else 0
if top_level_count + inline_count > MAX_ATTACHMENT_REFS_PER_REQUEST:
    return RoomCenterUserMessageResponse(
        message_id=None, message=None, success=False,
        error=f"Too many attachment references ({top_level_count + inline_count}); "
              f"maximum {MAX_ATTACHMENT_REFS_PER_REQUEST} per request",
        status_code=400,
    )

# Now safe to iterate (bounded by MAX_ATTACHMENT_REFS_PER_REQUEST)
inline_file_ids: list[str] = []
if raw_inline_attachments and isinstance(raw_inline_attachments, list):
    for item in raw_inline_attachments:
        fid = item.get("file_id") if isinstance(item, dict) else None
        if fid and isinstance(fid, str):
            inline_file_ids.append(fid)

room_center_request = RoomCenterUserMessageRequest(
    room_id=room_id, message=message, attachments=attachments,
    inline_file_ids=inline_file_ids or None,  # NEW — extracted from raw dict
)
```

Apply this to **both** `send_message()` and `create_and_parse_user_message()`.

#### 5.4.1 Dual-Source Attachment Merge Strategy

Attachments can arrive from two places:

| Source | Location | What it contains |
|--------|----------|-----------------|
| Top-level `attachments` | `request_data["attachments"]` -> `RoomCenterUserMessageRequest.attachments` | `file_id` only (via `UserAttachmentRequest`; metadata resolved server-side) |
| Inline in message | `request_data["message"]["message_content"]["attachments"]` (frontend pre-populates) | Arbitrary dicts with at least `file_id`; extracted from raw dict **before** Pydantic model construction (see 5.4 pseudocode). Only `file_id` is used; rest discarded. Stored on `RoomCenterUserMessageRequest.inline_file_ids`. |

**Both sources are accepted, but all metadata is re-resolved server-side.**

The server collects `file_id` values from both sources, deduplicates, resolves
each against `file_uploads` (section 5.5), and writes back the server-authoritative
result. This means:

- **Top-level only**: Normal path. file_ids resolved, metadata from DB.
- **Inline only**: Frontend-provided metadata is **discarded**; only `file_id` is
  extracted, then resolved server-side. This supports clients that embed attachments
  directly in the message body.
- **Both present**: file_ids are merged and deduplicated. The merged set is
  resolved server-side. No conflict — same file_id from both sources yields one
  attachment.
- **Neither present**: Text-only message, no resolution needed.

**Security guarantees** (preserved regardless of source):

- MIME type spoofing blocked (server resolves from `file_uploads`, never trusts client)
- Cross-room file reference blocked (server validates `room_id` on every file_id)
- Two-tier count limits: `MAX_ATTACHMENT_REFS_PER_REQUEST` (50) rejects absurd raw counts at API layer before dedup; `MAX_ATTACHMENTS_PER_MESSAGE` (10) enforces the semantic limit on the merged+deduped set in `_resolve_attachments()`
- `file_url` never persisted (server leaves it `None`; DB helper strips it)
- `content_summary` always server-generated (client-provided value overwritten)
- Service-layer trust boundary: `attachments` and `content_summary` are **unconditionally cleared** before resolution, even if `file_ids` is empty (guards against bypass of API-layer extraction)

```python
# In the shared attachment resolution (section 5.5), BEFORE persistence:
# 1. Collect file_ids from BOTH sources, deduplicate
file_ids: list[str] = []
seen: set[str] = set()

# Top-level attachments (primary source — typed as UserAttachmentRequest)
if request.attachments:
    for att in request.attachments:
        if att.file_id not in seen:
            file_ids.append(att.file_id)
            seen.add(att.file_id)

# Inline file_ids (secondary source — extracted from raw dict at API layer,
# already just strings; see section 5.4 pseudocode)
if request.inline_file_ids:
    for fid in request.inline_file_ids:
        if fid not in seen:
            file_ids.append(fid)
            seen.add(fid)

# 2. Clear client-controlled fields — trust boundary at service layer.
# Unconditionally wipe attachments + content_summary so that even if a future
# caller bypasses the API-layer inline extraction, no pre-filled data leaks.
if user_message.message_content:
    user_message.message_content.attachments = None
    user_message.message_content.content_summary = None

# 3. Resolve merged file_ids server-side
if file_ids:
    resolved = await self._resolve_attachments(file_ids, request.room_id)
    if isinstance(resolved, RoomCenterUserMessageResponse):
        return resolved  # validation error
    user_message.message_content.attachments = resolved.attachments
    user_message.message_content.content_summary = resolved.content_summary
```

### 5.5 Shared Attachment Resolution (`services/room_services.py`)

Both `send_message_to_room()` and `create_and_parse_user_message()` share the
same resolution logic. Extract it into a private helper to satisfy DRY:

```python
@dataclass
class _ResolvedAttachments:
    attachments: list[UserAttachment]
    content_summary: dict | None

async def _resolve_attachments(
    self,
    file_ids: list[str],
    room_id: str,
) -> _ResolvedAttachments | RoomCenterUserMessageResponse:
    """Resolve file_id list to server-authoritative UserAttachment objects.

    Accepts a pre-deduplicated list of file_ids (from both top-level and
    inline sources). All metadata is resolved from the file_uploads collection.
    Returns _ResolvedAttachments on success, or an error response on failure.
    """
    if len(file_ids) > MAX_ATTACHMENTS_PER_MESSAGE:
        return RoomCenterUserMessageResponse(
            message_id=None, message=None, success=False,
            error=f"Maximum {MAX_ATTACHMENTS_PER_MESSAGE} attachments per message",
            status_code=400,
        )

    attachments: list[UserAttachment] = []
    for file_id in file_ids:
        file_meta = await mongodb.file_uploads_collection.find_one(
            {"file_id": file_id, "room_id": room_id}
        )
        if not file_meta:
            return RoomCenterUserMessageResponse(
                message_id=None, message=None, success=False,
                error=f"File {file_id} not found", status_code=404,
            )
        attachments.append(UserAttachment(
            file_id=file_id,
            s3_key=file_meta["s3_key"],
            mime_type=file_meta["mime_type"],
            file_name=file_meta["file_name"],
            size_bytes=file_meta["size_bytes"],
            # file_url is NOT set here; populated only at read time (section 5.6)
        ))

    content_summary = None
    if attachments:
        mime_types = [a.mime_type for a in attachments]
        content_summary = {
            "has_images": any(m.startswith("image/") for m in mime_types),
            "has_files": any(not m.startswith("image/") for m in mime_types),
            "attachment_count": len(attachments),
            "mime_types": mime_types,
        }

    return _ResolvedAttachments(attachments=attachments, content_summary=content_summary)
```

#### 5.5.1 Integration into Both Entry Points

Both `send_message_to_room()` and `create_and_parse_user_message()` apply the
same pre-persistence logic:

```python
# Shared pre-persistence attachment merge + resolution (used by BOTH methods):
user_message = request.message

# Step 1: Collect file_ids from both sources, deduplicate (order-preserving)
file_ids: list[str] = []
seen: set[str] = set()

# Top-level attachments (typed as UserAttachmentRequest)
if request.attachments:
    for att in request.attachments:
        if att.file_id not in seen:
            file_ids.append(att.file_id)
            seen.add(att.file_id)

# Inline file_ids (extracted from raw dict at API layer; see section 5.4)
if request.inline_file_ids:
    for fid in request.inline_file_ids:
        if fid not in seen:
            file_ids.append(fid)
            seen.add(fid)

# Step 2: Clear client-controlled fields — trust boundary at service layer.
# Unconditionally wipe attachments + content_summary so that even if a future
# caller bypasses the API-layer inline extraction, no pre-filled data leaks.
if user_message.message_content:
    user_message.message_content.attachments = None
    user_message.message_content.content_summary = None

# Step 3: Resolve merged file_ids server-side
if file_ids:
    resolved = await self._resolve_attachments(file_ids, request.room_id)
    if isinstance(resolved, RoomCenterUserMessageResponse):
        return resolved  # validation error
    user_message.message_content.attachments = resolved.attachments
    user_message.message_content.content_summary = resolved.content_summary

# Step 4: Persist (message now contains server-validated attachments)
# _persist_user_message(user_message) / add_room_user_message(user_message)
```

For `create_and_parse_user_message()` specifically, the same injection runs
before `self.database_service.add_room_user_message(message)` (line 1863),
and the resolved attachments are also threaded into `RoomCenterMemoryRequest`:

```python
# In create_and_parse_user_message() — after attachment resolution:
room_memory_request = RoomCenterMemoryRequest(
    room_id=room_id,
    memory_content=message.message_content.message_text,
    attachments=message.message_content.attachments,  # resolved server-side
)
await self.room_memory_service.initialize_or_update_room_memory(room_memory_request)
```

### 5.6 Message Retrieval with Presigned URLs

In `inquiryRoomMessagesByRoomId`, after fetching messages from MongoDB,
batch-generate presigned URLs for all attachments:

```python
# Collect all s3_keys from user messages with attachments
s3_keys = []
for msg in user_messages:
    if msg.message_content and msg.message_content.attachments:
        for att in msg.message_content.attachments:
            s3_keys.append(att.s3_key)

# Batch generate presigned URLs (uses cache internally)
url_map = {}
if s3_keys:
    url_map = await s3_service.batch_presigned_urls(s3_keys)

# Inject file_url directly onto the model objects (not stored in DB).
# UserAttachment.file_url is an Optional field, so Pydantic allows mutation.
for msg in user_messages:
    if msg.message_content and msg.message_content.attachments:
        for att in msg.message_content.attachments:
            att.file_url = url_map.get(att.s3_key, "")
```

After this, when the message is serialized for the API response (via `.model_dump()`
or FastAPI's auto-serialization), each attachment includes `file_url` with a valid
presigned URL. On the **write** path, `file_url` defaults to `None` and is stripped
from the serialized document by the `_strip_file_urls()` helper in `database/mongodb.py`
(see section 5.1) before insertion.

**Key design decision**: `file_url` is generated at read time, never stored in MongoDB.
This ensures URLs never go stale in the database. The presigned URL cache (30min TTL)
avoids regeneration cost on rapid page refreshes.

### 5.7 Conversation Turn Annotation for Context Assembly

When recording a `ConversationTurn` for a user message with attachments, append
a structured text annotation to the `content` field. No schema change to
`ConversationTurn` is needed.

```python
def _human_size(size_bytes: int) -> str:
    """Format bytes as human-readable string: 512B, 245KB, 1.2MB."""
    if size_bytes < 1024:
        return f"{size_bytes}B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.0f}KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f}MB"


def build_turn_content(message_text: str, attachments: list[UserAttachment] | None) -> str:
    """Build conversation turn content with optional attachment annotations.

    The annotation is plain text that naturally flows into all downstream
    consumers (supervisor, context assembly, memory search, fact extraction)
    without schema changes.
    """
    content = message_text or ""
    if attachments:
        descriptions = []
        for att in attachments:
            size_str = _human_size(att.size_bytes) if att.size_bytes else "unknown size"
            descriptions.append(f"{att.file_name} ({att.mime_type}, {size_str})")
        content += f"\n[Attachments: {', '.join(descriptions)}]"
    return content
```

This means:
- The supervisor sees `"[Attachments: photo.png (image/png, 245KB)]"` and can
  route to vision-capable agents
- Context assembly includes it in the conversation history naturally
- Memory search indexes the annotation text
- Token estimation counts the annotation (~10-15 tokens, negligible)
- Compaction works as-is (annotation is part of the text string)

### 5.8 Memory Service Integration (`services/memory_service.py`)

`ConversationTurn` objects for user messages are created in
`memory_service.initialize_or_update_room_memory()` (definition at line 454,
turn creation at line ~516):

```python
turn = ConversationTurn(
    role=TurnRole.USER,
    content=clean_message,        # currently just the text
    user_id=user_id,
    estimated_tokens_full=estimate_tokens(clean_message),
    turn_notes=extract_turn_notes(clean_message),
)
```

The method takes a `RoomCenterMemoryRequest` object (not individual params).
To thread attachments, **add `attachments` to `RoomCenterMemoryRequest`** and
apply `build_turn_content()` inside the method:

**Step 1**: Extend `RoomCenterMemoryRequest` (`models/request.py` line 256):

```python
class RoomCenterMemoryRequest(BaseModel):
    room_id: str | None = None
    memory_id: str | None = None
    memory_content: str | None = None
    memory_created_at: datetime | None = None
    extend_info: dict[str, Any] | None = None
    memory: RoomMemory | None = None
    room_agent_set: dict[str, str] | None = None
    user_id: str | None = None
    attachments: list[UserAttachment] | None = None  # NEW
```

**Step 2**: Inside `initialize_or_update_room_memory()`, apply annotation
when building the turn (the method signature stays unchanged):

```python
async def initialize_or_update_room_memory(
    self, request: RoomCenterMemoryRequest
) -> RoomCenterMemoryResponse:
    # ... existing code ...
    new_message = request.memory_content

    # Apply attachment annotation to turn content
    turn_content = build_turn_content(new_message, request.attachments)
    turn = ConversationTurn(
        role=TurnRole.USER,
        content=turn_content,
        user_id=user_id,
        estimated_tokens_full=estimate_tokens(turn_content),
        turn_notes=extract_turn_notes(turn_content),
    )
```

**Step 3**: The caller in `room_services._initialize_room_memory()` (line 1637)
passes the resolved attachments via the request object:

```python
room_memory_initialize_or_update_response = (
    await self.room_memory_service.initialize_or_update_room_memory(
        RoomCenterMemoryRequest(
            room_id=request.room_id,
            memory_content=user_message.message_content.message_text,
            room_agent_set=room_agent_set,
            user_id=user_message.user_id,
            attachments=user_message.message_content.attachments,  # NEW
        )
    )
)
```

This preserves the existing request-object calling pattern with minimal diff.

### 5.9 RoomMessageCenter Attachment Propagation (`modules/RoomMessageCenter.py`)

`_process_supervisor_v2()` (defined at line 409; the `supervisor_executor.run()` call
with `message_text=` is at line ~518-522) extracts message text and passes it to
the supervisor executor:

```python
message_text=user_message.message_content.message_text or "",
```

With attachments stored on `MessageContent`, the text annotation must be applied
here so the supervisor sees it:

```python
# Build annotated text so supervisor knows about attachments
message_text = build_turn_content(
    user_message.message_content.message_text or "",
    user_message.message_content.attachments,
)
```

The `attachments` list also needs to be threaded through to the agent message
building code so `_build_message_parts()` (section 6.3) can construct `FilePart`
objects. Pass it via the supervisor executor context or as a parameter to
`room_services.create_agent_message()`.

### 5.10 SSE `agent_response` Enhancement

Update `send_agent_response` in `services/sse_services.py` to optionally include
non-text parts when available:

```python
async def send_agent_response(
    self,
    room_id: str,
    message_id: str,
    agent_id: str,
    content: str,
    related_message_id: str = None,
    parts: list[dict] | None = None,  # NEW: non-text parts
):
    data = {
        "message_id": message_id,
        "agent_id": agent_id,
        "content": content,
        "related_message_id": related_message_id,
        "timestamp": utcnow().isoformat(),
    }
    if parts:
        data["parts"] = parts
    await self.broadcast_to_room(room_id, "agent_response", data)
```

The `parts` field is additive. The existing frontend handler reads `content` and
ignores unknown fields, so this is backward-compatible.

---

## 6. Phase 3 -- A2A Multimodal Negotiation

**Goal**: Stop blocking non-text agent output, send user attachments to agents as
`FilePart`, unify part extraction logic, and ensure **all agent output paths**
(ResponseProcessor streaming, sync responses, webhook/task notifications) carry
non-text content to the frontend.

**Files to modify**: `services/a2a_service.py`, `common/utils/a2a_helpers.py`,
`services/room_services.py`, `modules/ResponseProcessor.py`, `api/webhooks.py`,
`services/task_notification_service.py`, `services/sse_services.py`,
`services/content_storage_service.py`

### 6.1 Dynamic Output Mode Negotiation (`services/a2a_service.py`)

Replace the 4 hardcoded `acceptedOutputModes=["text/plain"]` calls (lines 319, 518,
571, 635) with a dynamic intersection:

```python
PLATFORM_SUPPORTED_MODES = {
    "text/plain",
    "image/png", "image/jpeg", "image/gif", "image/webp",
    "application/json",
}

def _resolve_accepted_modes(self, agent_card: AgentCard) -> list[str]:
    """Intersect agent's output modes with platform capabilities.

    PLATFORM_SUPPORTED_MODES is the single source of truth for what the
    frontend can render. Expand this set as frontend rendering capabilities
    are added.
    """
    agent_modes = set(getattr(agent_card, 'default_output_modes', ['text']))

    # Shorthand-to-MIME expansion. 'image' expands to ALL platform-supported
    # image types, not just one. This avoids incorrectly narrowing negotiation
    # when an agent declares 'image' (which may mean jpeg, webp, etc.).
    MODE_TO_MIMES: dict[str, set[str]] = {
        'text': {'text/plain'},
        'image': {'image/png', 'image/jpeg', 'image/gif', 'image/webp'},
        'json': {'application/json'},
        'form': {'text/plain'},
        'markdown': {'text/plain'},
    }

    agent_mime_modes = set()
    for mode in agent_modes:
        if '/' in mode:
            agent_mime_modes.add(mode)
        elif mode in MODE_TO_MIMES:
            agent_mime_modes.update(MODE_TO_MIMES[mode])
        else:
            agent_mime_modes.add('text/plain')

    accepted = agent_mime_modes & PLATFORM_SUPPORTED_MODES
    if not accepted:
        accepted = {'text/plain'}
    return sorted(accepted)
```

All 4 call sites change from:
```python
configuration=MessageSendConfiguration(accepted_output_modes=["text/plain"])
```
to:
```python
accepted = self._resolve_accepted_modes(agent_card)
configuration=MessageSendConfiguration(accepted_output_modes=accepted)
```

The `agent_card` parameter is already available at all 4 call sites (either as a
direct parameter or accessible from the method's scope).

**Important -- Dual type system**: `a2a_service.py` imports `AgentCard` from
`a2a.types` (SDK), which uses **snake_case** field names (`default_output_modes`).
The local `AgentCard` in `common/types.py` uses **camelCase** (`defaultOutputModes`).
Code in `a2a_service.py` must use the SDK names. The existing production code uses
`acceptedOutputModes` (camelCase alias) which triggers deprecation warnings -- the
fix above also corrects this to `accepted_output_modes`.

### 6.2 Unified Part Extraction (`common/utils/a2a_helpers.py`)

Replace the two inconsistent text-only extractors with a unified function:

```python
from dataclasses import dataclass, field

@dataclass
class ExtractedParts:
    """Structured extraction result from A2A parts."""
    text_parts: list[str] = field(default_factory=list)
    file_parts: list[dict] = field(default_factory=list)
    data_parts: list[dict] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(self.text_parts)

    @property
    def has_non_text(self) -> bool:
        return bool(self.file_parts or self.data_parts)


def extract_parts(parts: list) -> ExtractedParts:
    """Extract and classify all parts from an A2A parts list.

    Handles both direct part objects and discriminated union wrappers (part.root).
    This is the single source of truth for part extraction logic.
    """
    result = ExtractedParts()
    for part in parts:
        root = getattr(part, "root", part)
        kind = getattr(root, "kind", None)

        if kind == "text":
            text = getattr(root, "text", None)
            if text:
                result.text_parts.append(text)
        elif kind == "file":
            result.file_parts.append(
                root.model_dump() if hasattr(root, "model_dump") else vars(root)
            )
        elif kind == "data":
            result.data_parts.append(
                root.model_dump() if hasattr(root, "model_dump") else vars(root)
            )
        else:
            # Unknown part type -- try text extraction as fallback
            text = getattr(root, "text", None)
            if text:
                result.text_parts.append(text)
            else:
                logger.warning("Unknown part kind=%s, skipping", kind)
    return result


def extract_parts_from_artifacts(artifacts: list) -> ExtractedParts:
    """Extract parts from a list of A2A artifacts."""
    result = ExtractedParts()
    for artifact in artifacts:
        if not artifact.parts:
            continue
        artifact_parts = extract_parts(artifact.parts)
        result.text_parts.extend(artifact_parts.text_parts)
        result.file_parts.extend(artifact_parts.file_parts)
        result.data_parts.extend(artifact_parts.data_parts)
    return result
```

Backward-compatible wrappers (keep existing function signatures):

```python
def get_text_from_message(message: Message | None) -> str:
    """Extract text from a Message object. Backward-compatible wrapper."""
    if message is None:
        return ""
    return extract_parts(message.parts).text


def extract_text_from_artifacts(artifacts: list) -> str | None:
    """Extract text from artifacts. Backward-compatible wrapper."""
    text = extract_parts_from_artifacts(artifacts).text
    return text if text else None
```

All existing callers continue to work unchanged. New code can use `extract_parts()`
directly to access non-text content.

### 6.3 Outbound `FilePart` Construction (`services/room_services.py`)

When building A2A messages to send to agents, include `FilePart` for user attachments.

In `create_task_for_agent()` (line 703), `create_task_for_agents_group()` (line 752),
and `_generate_agent_message_content()` (line 798, used by the V2 supervisor path),
after creating the `TextPart`, check for attachments:

```python
# Imports at top of services/room_services.py:
# from a2a.types import FileWithUri  (add to existing imports from a2a.types)

async def _build_message_parts(
    self,
    text: str,
    attachments: list[UserAttachment] | None,
    agent_card: AgentCard,
) -> list[Part]:
    """Build A2A message parts from text and optional attachments.

    If the agent supports file input (check default_input_modes), include
    FilePart with presigned S3 URIs. Otherwise, the text annotation
    from build_turn_content() already describes the attachments.

    Note: Uses SDK AgentCard field names (snake_case). The local AgentCard
    in common/types.py uses camelCase -- check which type is in scope.
    """
    parts = [TextPart(text=text)]

    if not attachments:
        return parts

    # SDK AgentCard: default_input_modes (snake_case)
    # Local AgentCard: defaultInputModes (camelCase)
    # Use `is None` check (not truthiness) to handle empty lists correctly.
    agent_input_modes_raw = getattr(agent_card, 'default_input_modes', None)
    if agent_input_modes_raw is None:
        agent_input_modes_raw = getattr(agent_card, 'defaultInputModes', None)
    agent_input_modes = set(agent_input_modes_raw or ['text'])

    # Determine if the agent can accept FilePart.
    #
    # Strategy: exact keyword match OR explicit MIME types known to be binary.
    # We intentionally exclude `application/json` (which signals DataPart/structured
    # data capability, not binary file upload). The `application/` prefix is NOT
    # used as a blanket match because it would false-positive on JSON-only agents.
    FILE_CAPABLE_EXACT = frozenset({
        'file',           # A2A shorthand for "accepts FilePart"
        '*/*',            # wildcard = accepts anything
    })
    FILE_CAPABLE_PREFIXES = frozenset({
        'image/', 'audio/', 'video/',
    })
    FILE_CAPABLE_MIMES = frozenset({
        'application/pdf',
        'application/octet-stream',
        'application/zip',
        'application/x-tar',
        'application/gzip',
    })
    supports_files = bool(
        agent_input_modes & FILE_CAPABLE_EXACT
        or agent_input_modes & FILE_CAPABLE_MIMES
        or any(
            any(m.startswith(prefix) for prefix in FILE_CAPABLE_PREFIXES)
            for m in agent_input_modes
        )
    )

    if supports_files:
        for att in attachments:
            presigned_url = await self._s3_service.generate_presigned_url(att.s3_key)
            parts.append(FilePart(file=FileWithUri(
                uri=presigned_url,
                mime_type=att.mime_type,
                name=att.file_name,
            )))

    return parts
```

For agents that don't support file input, the `TextPart` already contains the
text annotation (from section 5.7), so the agent sees
`"[Attachments: photo.png (image/png, 245KB)]"` and can respond accordingly
(e.g., "I can't process images, please describe what you see").

### 6.4 Agent Response Non-Text Part Handling (`modules/ResponseProcessor.py`)

When agents return non-text content, it can arrive in two places: **message parts**
(streaming `message` events or sync responses) and **artifact parts** (streaming
`artifact-update` events). The current code only handles text in messages and
only passes through raw artifacts. This section addresses the message path;
section 6.6 addresses the artifact path.

#### 6.4.1 Streaming Message Chunks (`_handle_stream_message_chunk`, line 436)

Currently (line 449-452), only text is extracted from streaming message parts:

```python
# Current -- only text, FilePart/DataPart silently dropped:
content = "".join(
    part.root.text if part.root and hasattr(part.root, "text") else ""
    for part in message_list
)
```

Replace with the unified `extract_parts()`:

```python
from common.utils.a2a_helpers import extract_parts

extracted = extract_parts(message_list)
content = extracted.text
streaming_state.full_response_text += content

# If non-text parts are present, convert inline base64 bytes to S3 URIs
# (reuses _convert_inline_bytes_to_s3 logic from section 6.6) and
# track them for inclusion in the final agent_response SSE event.
if extracted.has_non_text:
    for file_part in extracted.file_parts:
        raw_bytes = file_part.get("file", {}).get("bytes")
        if raw_bytes:
            if streaming_state.inline_conversion_count >= MAX_INLINE_CONVERSIONS_PER_MESSAGE:
                logger.warning("Inline conversion cap reached; leaving base64 as-is")
                break
            try:
                import base64
                decoded = base64.b64decode(raw_bytes)
                mime = file_part.get("file", {}).get("mime_type", "application/octet-stream")
                ext = mime.split("/")[-1] if "/" in mime else "bin"
                idx = len(streaming_state.non_text_parts)
                s3_key = f"artifacts/{ctx.room_id}/{ctx.current_message.message_id}/{idx}.{ext}"
                await self._s3_service.upload_file(io.BytesIO(decoded), s3_key, mime, len(decoded))
                url = await self._s3_service.generate_presigned_url(s3_key)
                file_part["file"]["bytes"] = None
                file_part["file"]["uri"] = url
                streaming_state.inline_conversion_count += 1
            except Exception:
                logger.error("Failed S3 upload for streaming file part", exc_info=True)
    streaming_state.non_text_parts.extend(extracted.file_parts)
    streaming_state.non_text_parts.extend(extracted.data_parts)
```

Add `non_text_parts` to `MessageStreamingState`:

```python
@dataclass
class MessageStreamingState:
    full_response_text: str = ""
    accumulated_parts: list[Part] = field(default_factory=list)
    non_text_parts: list[dict] = field(default_factory=list)  # NEW
    inline_conversion_count: int = 0  # tracks base64-to-S3 conversions (capped)
    agent_message_id: str | None = None
    message_added_to_history: bool = False
```

#### 6.4.2 Sync Response Parsing (`_parse_sync_fallback_response`, line 710)

Currently (line 727-736), only text is extracted from sync response parts:

```python
# Current -- FilePart/DataPart silently dropped:
texts = []
for part in result.parts or []:
    if hasattr(part, "text") and part.text:
        texts.append(part.text)
    elif hasattr(part, "root") and hasattr(part.root, "text"):
        texts.append(part.root.text)
return {"type": "message", "message_id": message_id, "content": "".join(texts)}
```

Replace with:

```python
extracted = extract_parts(result.parts or [])
response_dict = {
    "type": "message",
    "message_id": message_id,
    "content": extracted.text,
}
if extracted.has_non_text:
    response_dict["parts"] = extracted.file_parts + extracted.data_parts
return response_dict
```

Then in `_process_sync_response()` (line 925), convert inline base64 before SSE
broadcast (same protection as the streaming path):

```python
non_text_parts = response.get("parts")

# Convert any inline base64 file parts to S3 URIs before SSE broadcast
if non_text_parts:
    for part in non_text_parts:
        raw_bytes = part.get("file", {}).get("bytes") if part.get("kind") == "file" else None
        if raw_bytes:
            try:
                import base64
                decoded = base64.b64decode(raw_bytes)
                mime = part.get("file", {}).get("mime_type", "application/octet-stream")
                ext = mime.split("/")[-1] if "/" in mime else "bin"
                s3_key = f"artifacts/{room_id}/{message_id}/sync_{non_text_parts.index(part)}.{ext}"
                await self._s3_service.upload_file(io.BytesIO(decoded), s3_key, mime, len(decoded))
                url = await self._s3_service.generate_presigned_url(s3_key)
                part["file"]["bytes"] = None
                part["file"]["uri"] = url
            except Exception:
                logger.error("Failed S3 upload for sync response file part", exc_info=True)

# ... existing logic ...
if send_sse:
    await self.sse_manager.send_agent_response(
        room_id=room_id,
        message_id=message_id,
        agent_id=current_message.agent_id,
        content=full_response_text,
        parts=non_text_parts,  # NEW -- non-text parts forwarded to frontend
    )
```

#### 6.4.3 Finalize Streaming (`_finalize_streaming`, line 610)

Currently (line 632-634), only `full_response_text` is stored:

```python
if streaming_state.full_response_text:
    ctx.current_message.message_content.message_text = (
        streaming_state.full_response_text
    )
```

After finalization, send accumulated non-text parts via SSE so the frontend
can render them:

```python
if streaming_state.non_text_parts and ctx.send_sse:
    await self.sse_manager.send_agent_response(
        ctx.room_id,
        ctx.current_message.message_id,
        ctx.current_message.agent_id,
        streaming_state.full_response_text,
        parts=streaming_state.non_text_parts,
    )
```

Non-text parts from agent messages are preserved in the `Task.history` message
parts (the `accumulated_parts` are already stored there at line 460-464). The
frontend renders them by inspecting `task.history` message parts alongside the
text content.

### 6.5 Webhook / Task Notification Multimodal Path

The webhook path (`api/webhooks.py`) and task notification service
(`services/task_notification_service.py`) are a **separate code path** from
`ResponseProcessor` for long-running tasks. They currently extract text only:

- `webhooks.py` line 186: `extract_text_from_artifacts(updated_task.artifacts)`
- `task_notification_service.py` line 150: `extract_text_from_artifacts(task.artifacts)`

Both feed `content: str` into `send_task_update()`, which sends an SSE
`task_update` event with only a text `content` field.

**Changes needed**:

1. **Webhook handler** (`api/webhooks.py` line 183-186): Use `extract_parts_from_artifacts()`
   instead of `extract_text_from_artifacts()` to capture non-text parts:

```python
# Before:
task_result_text = extract_text_from_artifacts(updated_task.artifacts)

# After:
from common.utils.a2a_helpers import extract_parts_from_artifacts
extracted = extract_parts_from_artifacts(updated_task.artifacts)
task_result_text = extracted.text
task_result_parts = (extracted.file_parts + extracted.data_parts) if extracted.has_non_text else None
```

2. **Task notification service** (`task_notification_service.py` line 148-150):
   Same extraction change, plus thread parts through to SSE:

```python
# In notify_task_update():
if task and state == TaskState.completed and task.artifacts:
    extracted = extract_parts_from_artifacts(task.artifacts)
    content = extracted.text if extracted.text else None
    non_text_parts = (extracted.file_parts + extracted.data_parts) if extracted.has_non_text else None
```

3. **SSE `send_task_update`** (`services/sse_services.py` line 394): Add optional
   `parts` parameter (same pattern as `send_agent_response`):

```python
async def send_task_update(
    self,
    room_id: str,
    message_id: str,
    status: Any,
    content: str | None = None,
    parts: list[dict] | None = None,  # NEW: non-text parts from artifacts
    # ... existing params ...
):
    data = {
        # ... existing fields ...
    }
    if parts:
        data["parts"] = parts
    await self.broadcast_to_room(room_id, "task_update", data)
```

4. **Queue continuation** (`webhooks.py` line 200-204): `task_result_text` is passed
   to `resume_queue_continuation()`. If the next agent in the queue needs the full
   artifact content (including files), thread `task_result_parts` through as well.

**Files to modify**: `api/webhooks.py`, `services/task_notification_service.py`,
`services/sse_services.py`

### 6.6 Base64-to-S3 Conversion in Artifact Processing

In `ResponseProcessor._handle_stream_artifact_update()` (lines 567-608 of
`modules/ResponseProcessor.py`), before broadcasting via SSE, check for `FilePart`
with inline `bytes` and convert to S3 URI:

```python
MAX_INLINE_CONVERSIONS_PER_MESSAGE = 20

async def _convert_inline_bytes_to_s3(
    self,
    artifact,
    room_id: str,
    message_id: str,
) -> None:
    """Convert any inline base64 bytes in artifact parts to S3 URIs.

    Some agents return images as base64 in FilePart.file.bytes instead of
    URIs. This inflates SSE payloads (10MB image = 13.3MB base64 JSON).
    Converting to S3 keeps SSE payloads small.

    Mutates the artifact in place.
    """
    if not artifact.parts:
        return

    for i, part in enumerate(artifact.parts):
        root = getattr(part, "root", part)
        if getattr(root, "kind", None) != "file":
            continue
        file_content = getattr(root, "file", None)
        if not file_content:
            continue
        raw_bytes = getattr(file_content, "bytes", None)
        if not raw_bytes:
            continue

        # Decode base64 and upload to S3
        import base64
        decoded = base64.b64decode(raw_bytes)
        mime = getattr(file_content, "mime_type", None) or getattr(file_content, "mimeType", "application/octet-stream")
        ext = mime.split("/")[-1] if "/" in mime else "bin"
        s3_key = f"artifacts/{room_id}/{message_id}/{i}.{ext}"

        try:
            await self._s3_service.upload_file(
                file_data=io.BytesIO(decoded),
                s3_key=s3_key,
                content_type=mime,
                content_length=len(decoded),
            )

            presigned_url = await self._s3_service.generate_presigned_url(s3_key)

            # Replace bytes with URI (mutate in place)
            file_content.bytes = None
            file_content.uri = presigned_url
        except Exception:
            logger.error(
                "Failed to upload inline base64 to S3: room=%s message=%s part=%d",
                room_id, message_id, i, exc_info=True,
            )
            # Leave original bytes in place -- frontend handles gracefully
```

Call this in `_handle_stream_artifact_update()` before `persist_message` and
`send_artifact_update`:

```python
async def _handle_stream_artifact_update(self, result, ctx):
    artifact_result = getattr(result, "artifact", None)
    # ... existing artifact accumulation logic ...

    # Convert inline base64 to S3 URIs before persistence and SSE
    await self._convert_inline_bytes_to_s3(
        artifact_result, ctx.room_id, ctx.current_message.message_id
    )

    await self.tsm.persist_message(ctx.current_message)
    if ctx.send_sse:
        await self.sse_manager.send_artifact_update(...)
```

### 6.7 ContentStorageService S3 Expansion

With `S3Service` available from Phase 1, the `NotImplementedError` in
`content_storage_service.py` line 228 can be replaced.

**Important contract**: `expand_content_reference()` returns `str` — the **full
content string**, not a URL. All callers (e.g., `compaction_service.py` line 408,
`fetch_turn_content()`) expect to receive the actual text content for context
assembly. Returning a presigned URL would break this contract.

For the S3 storage type, the implementation must **download** the content:

```python
elif content_ref.storage_type == StorageType.S3:
    if not content_ref.s3_key:
        raise ValueError(f"ContentReference for turn {turn_id} has no s3_key")

    # Download the actual content from S3 (not a presigned URL).
    # S3-stored compaction content is text that was too large for MongoDB
    # but still needs to be fully expanded for context assembly.
    content = await self._s3_service.download_text(content_ref.s3_key)
    if content is None:
        raise ContentExpiredError(turn_id, content_ref.s3_key)
    return content
```

Add `download_text()` to `S3Service`:

```python
async def download_text(self, s3_key: str) -> str | None:
    """Download a text file from S3 and return its content as a string.

    Used by compaction expansion to retrieve full turn content.
    Returns None if the object doesn't exist.
    """
    async with self._session.client("s3", region_name=self._region) as client:
        try:
            response = await client.get_object(Bucket=self._bucket, Key=s3_key)
            body = await response["Body"].read()
            return body.decode("utf-8")
        except client.exceptions.NoSuchKey:
            return None
```

This preserves the `expand_content_reference()` contract while using S3 storage.
The `S3Service` now has two retrieval patterns: `download_text()` for compaction
content (returns content), and `generate_presigned_url()` for user-facing files
(returns URL).

---

## 7. Phase 4 -- Tests

**Goal**: Comprehensive test coverage for all multimodal code paths.

**New files**: `tests/test_multimodal_part_extraction.py`,
`tests/test_file_upload.py`, `tests/test_mode_negotiation.py`,
`tests/test_multimodal_integration.py`, `tests/test_create_and_parse.py`,
`tests/test_dual_source_attachments.py`, `tests/test_file_url_exclusion.py`

### 7.1 Part Extraction Tests (`tests/test_multimodal_part_extraction.py`)

```python
import pytest
from a2a.types import TextPart, FilePart, DataPart, FileWithUri, Message, Role
from common.utils.a2a_helpers import (
    extract_parts, extract_parts_from_artifacts,
    get_text_from_message, extract_text_from_artifacts,
)

class TestExtractParts:
    """Tests for the unified extract_parts() function."""

    def test_text_only(self):
        parts = [TextPart(text="hello"), TextPart(text=" world")]
        result = extract_parts(parts)
        assert result.text == "hello world"
        assert result.file_parts == []
        assert result.data_parts == []
        assert not result.has_non_text

    def test_file_only(self):
        parts = [FilePart(file=FileWithUri(uri="s3://bucket/key", mime_type="image/png"))]
        result = extract_parts(parts)
        assert result.text == ""
        assert len(result.file_parts) == 1
        assert result.has_non_text

    def test_mixed_parts(self):
        parts = [
            TextPart(text="See this image: "),
            FilePart(file=FileWithUri(uri="s3://img.png", mime_type="image/png")),
        ]
        result = extract_parts(parts)
        assert result.text == "See this image: "
        assert len(result.file_parts) == 1
        assert result.has_non_text

    def test_data_part(self):
        parts = [DataPart(data={"key": "value"})]
        result = extract_parts(parts)
        assert len(result.data_parts) == 1

    def test_empty_parts(self):
        result = extract_parts([])
        assert result.text == ""
        assert not result.has_non_text

    # Malformed / edge cases
    def test_part_with_none_text(self):
        parts = [TextPart(text="")]
        result = extract_parts(parts)
        assert result.text == ""

class TestBackwardCompat:
    """Verify wrapper functions maintain exact existing behavior."""

    def test_get_text_from_message_text_only(self):
        msg = Message(role=Role.agent, message_id="m1",
                      parts=[TextPart(text="Hello"), TextPart(text=" world")])
        assert get_text_from_message(msg) == "Hello world"

    def test_get_text_from_message_ignores_files(self):
        msg = Message(role=Role.agent, message_id="m1", parts=[
            TextPart(text="text"),
            FilePart(file=FileWithUri(uri="s3://x", mime_type="image/png")),
        ])
        assert get_text_from_message(msg) == "text"

    def test_get_text_from_message_none(self):
        assert get_text_from_message(None) == ""

    def test_extract_text_from_artifacts_returns_none_when_empty(self):
        # Artifact with only file parts -> no text -> returns None
        # (backward compat: function returns None, not "")
        ...
```

### 7.2 File Upload Tests (`tests/test_file_upload.py`)

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from models.file_upload import ALLOWED_MIME_TYPES, MAX_FILE_SIZE_BYTES

@pytest.mark.asyncio
class TestFileUploadValidation:
    """Unit tests for FileUploadService validation."""

    @pytest.mark.parametrize("mime", list(ALLOWED_MIME_TYPES))
    async def test_allowed_mime_types_accepted(self, mime):
        ...

    @pytest.mark.parametrize("mime", ["application/exe", "text/html", "image/svg+xml"])
    async def test_disallowed_mime_types_rejected(self, mime):
        ...

    async def test_file_at_size_limit_accepted(self):
        ...

    async def test_file_over_size_limit_rejected(self):
        ...

    async def test_content_type_mismatch_rejected(self):
        ...

@pytest.mark.asyncio
class TestFileUploadS3:
    """Unit tests for S3 interaction (mocked)."""

    async def test_successful_upload_returns_metadata(self):
        ...

    async def test_s3_upload_failure_raises(self):
        ...

@pytest.mark.asyncio
class TestFileUploadEndpoint:
    """Handler-level tests for upload_file() (direct call, no TestClient).

    Follows codebase pattern from test_api_hitl.py: call handler directly
    with mocked dependencies.
    """

    async def test_upload_requires_room_ownership(self, mock_user):
        ...

    async def test_valid_upload_returns_file_metadata(self, mock_user, mock_s3_service):
        ...

    async def test_missing_room_id_returns_422(self, mock_user):
        ...
```

### 7.3 Mode Negotiation Tests (`tests/test_mode_negotiation.py`)

```python
import pytest

class TestResolveAcceptedModes:
    """Parametrized tests for _resolve_accepted_modes()."""

    @pytest.mark.parametrize("agent_modes,expected", [
        # Text-only agent
        (["text"], ["text/plain"]),
        # Image + text agent (image expands to all supported types)
        (["text", "image"], sorted([
            "image/png", "image/jpeg", "image/gif", "image/webp", "text/plain"
        ])),
        # Agent with MIME types
        (["text/plain", "image/jpeg"], ["image/jpeg", "text/plain"]),
        # JSON-only agent
        (["json"], ["application/json"]),
        # Unknown mode -> fallback to text
        (["hologram"], ["text/plain"]),
        # No output modes declared -> default text
        ([], ["text/plain"]),
        # Agent with mode not in platform set -> text fallback
        (["audio/mpeg"], ["text/plain"]),
        # Mixed shorthand and MIME
        (["text", "image/webp"], ["image/webp", "text/plain"]),
    ])
    def test_mode_resolution(self, agent_modes, expected):
        # Test uses SDK AgentCard (snake_case fields).
        # make_agent_card() should return an SDK AgentCard with
        # default_output_modes set to agent_modes.
        card = make_agent_card(default_output_modes=agent_modes)
        result = a2a_service._resolve_accepted_modes(card)
        assert sorted(result) == sorted(expected)


class TestBuildMessagePartsFileCapability:
    """Tests for supports_files whitelist in _build_message_parts()."""

    @pytest.mark.parametrize("input_modes,should_support_files", [
        # Explicit file support keywords
        (["file"], True),
        (["*/*"], True),
        # Binary MIME prefix matches
        (["image/png"], True),
        (["audio/mp3"], True),
        (["video/mp4"], True),
        # Explicit binary application types
        (["application/pdf"], True),
        (["application/octet-stream"], True),
        # Should NOT match: application/json is DataPart, not FilePart
        (["application/json"], False),
        # Should NOT match: text-like modes
        (["text/plain"], False),
        (["text/markdown"], False),
        (["text/html"], False),
        # Default text-only
        (["text"], False),
        ([], False),
        # Mixed: one file-capable mode is enough
        (["text", "image/png"], True),
        # JSON + file -> file wins
        (["application/json", "file"], True),
        # JSON-only agent should NOT receive FilePart
        (["text", "application/json"], False),
    ])
    async def test_file_capability_detection(self, input_modes, should_support_files):
        card = make_agent_card(default_input_modes=input_modes)
        ...
```

### 7.4 Turn Annotation & Memory Tests (`tests/test_turn_annotation.py`)

```python
import pytest

class TestBuildTurnContent:
    """Tests for build_turn_content() annotation logic (section 5.7)."""

    def test_text_only_returns_unchanged(self):
        """No attachments -> original text returned as-is."""

    def test_single_attachment_annotation(self):
        """One image attachment -> text prepended with
        '[Attachments: photo.png (image/png, 245KB)]'."""

    def test_multiple_attachments_annotation(self):
        """Multiple attachments -> comma-separated list in annotation."""

    def test_empty_attachments_list(self):
        """Empty list (not None) -> no annotation added."""

    def test_zero_byte_file_annotation(self):
        """Edge case: size_bytes=0 -> formats as '0B'."""

    def test_human_readable_size_formatting(self):
        """1048576 bytes -> '1.0MB', 512 -> '512B'."""


class TestContentSummaryGeneration:
    """Tests for content_summary dict built during message creation (section 5.5)."""

    def test_no_attachments_no_summary(self):
        """Message without attachments -> content_summary is None."""

    def test_with_attachments_generates_summary(self):
        """Message with attachments -> content_summary contains
        has_images, has_files, attachment_count, and mime_types."""
```

### 7.5 Message Retrieval & Presigned URL Tests (`tests/test_message_retrieval.py`)

```python
import pytest

@pytest.mark.asyncio
class TestPresignedUrlInjection:
    """Tests for batch presigned URL injection at read time (section 5.6)."""

    async def test_attachments_get_presigned_urls(self):
        """Messages with attachments -> each attachment.file_url populated."""

    async def test_no_attachments_no_s3_calls(self):
        """Messages without attachments -> S3Service not called."""

    async def test_expired_cache_regenerates_urls(self):
        """Presigned URL cache expired -> new URL generated."""

    async def test_batch_urls_uses_cache(self):
        """Multiple messages with same s3_key -> single S3 call (cached)."""
```

### 7.6 Error Handling Tests (`tests/test_multimodal_errors.py`)

```python
import pytest
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
class TestS3ErrorHandling:
    """Tests for graceful S3 failure handling."""

    async def test_s3_upload_failure_returns_500(self):
        """S3 upload error -> HTTPException(500) with logged error."""

    async def test_s3_presigned_url_failure_returns_500(self):
        """Presigned URL generation error -> HTTPException(500)."""

    async def test_base64_conversion_s3_failure_logged_not_crash(self):
        """S3 upload failure during base64 conversion in artifact
        processing -> error logged, streaming continues without file."""

    async def test_attachment_resolution_missing_file_id(self):
        """file_id not found in MongoDB -> RoomCenterUserMessageResponse(success=False,
        status_code=404). Not an HTTPException — uses the same business error contract
        as all other attachment errors from sendMessage/createAndParse."""
```

### 7.7 conftest.py Updates

Add the following to `tests/conftest.py`:

```python
# Add to PATCH dict:
PATCH.update({
    "files.file_upload_service": "api.files.file_upload_service",
    "files.s3_service": "services.s3_service.s3_service",
})


@pytest.fixture
def mock_s3_service():
    """Mock S3Service for unit tests."""
    mock = AsyncMock()
    mock.upload_file = AsyncMock(return_value=None)
    mock.generate_presigned_url = AsyncMock(return_value="https://s3.example.com/presigned")
    mock.batch_presigned_urls = AsyncMock(return_value={})
    return mock


@pytest.fixture
def sample_file_upload_metadata():
    """Factory for FileUploadMetadata test data."""
    return FileUploadMetadata(
        file_id="test-file-id",
        room_id="test-room-id",
        user_id="test-user-id",
        s3_key="uploads/test-room-id/test-file-id/test.png",
        mime_type="image/png",
        file_name="test.png",
        size_bytes=1024,
    )
```

### 7.8 Integration Test (`tests/test_multimodal_integration.py`)

```python
import pytest
from httpx import AsyncClient
from unittest.mock import AsyncMock

@pytest.mark.asyncio
class TestMultimodalFlow:
    """End-to-end multimodal message flow with mocked externals.

    NOTE: Integration tests use AsyncClient for full HTTP-level validation.
    Unit tests in 7.2-7.6 use direct handler calls (codebase convention).
    """

    async def test_user_upload_to_agent_filepart(self, app, mock_s3, mock_db):
        """Upload file -> send message with attachment -> verify outbound
        A2A message contains FilePart alongside TextPart."""
        async with AsyncClient(app=app, base_url="http://test") as client:
            # 1. Upload file
            upload_resp = await client.post("/api/v1/files/upload", ...)
            assert upload_resp.status_code == 200
            file_id = upload_resp.json()["file_id"]

            # 2. Send message with attachment (must use full RoomUserMessage structure)
            send_resp = await client.post("/api/v1/roomCenter/sendMessage", json={
                "room_id": "test-room",
                "message": {
                    "room_id": "test-room",
                    "message_id": "msg-001",
                    "message_type": "user",
                    "message_content": {
                        "message_text": "What's in this image?"
                    }
                },
                "attachments": [{"file_id": file_id}],
            })
            assert send_resp.status_code == 200

            # 3. Verify outbound A2A message has FilePart
            # (captured from mocked a2a_service.send_message_streaming)
            call_args = mock_a2a_service.send_message_streaming.call_args
            message = call_args.kwargs["message"]
            assert len(message.parts) == 2
            assert message.parts[0].root.kind == "text"
            assert message.parts[1].root.kind == "file"

    async def test_agent_artifact_with_image_reaches_sse(self, ...):
        """Simulate agent returning image artifact -> verify SSE
        artifact_update event contains the file part."""
        ...

    async def test_base64_artifact_converted_to_s3(self, ...):
        """Simulate agent returning base64 image -> verify it's uploaded
        to S3 and SSE contains URI (not bytes)."""
        ...
```

### 7.9 Regression Tests for Existing Paths

Multimodal changes touch several existing code paths. These existing test files
need **updates** (not just new test files) to verify backward compatibility and
prevent regressions:

**`tests/test_compaction_service.py`** (line 294 area):
- Current test asserts `NotImplementedError` for `StorageType.S3`. After section 6.7
  implementation, update to test that S3 expansion actually downloads content:

```python
@pytest.mark.asyncio
class TestS3Expansion:
    async def test_s3_expansion_returns_content_string(self, mock_s3_service):
        """S3 storage type -> downloads text content (not presigned URL)."""

    async def test_s3_expansion_missing_key_raises_value_error(self):
        """ContentReference with S3 type but no s3_key -> ValueError."""

    async def test_s3_expansion_missing_object_raises_expired(self, mock_s3_service):
        """S3 object doesn't exist -> ContentExpiredError."""
```

**`tests/test_api_room_center.py`** (line 419 area):
- Current `send_message` tests only send text. Add attachment variant:

```python
@pytest.mark.asyncio
class TestSendMessageWithAttachments:
    async def test_send_message_with_valid_attachments(self, mock_user):
        """sendMessage with attachments -> message persisted with resolved metadata."""

    async def test_send_message_with_invalid_file_id(self, mock_user):
        """sendMessage with nonexistent file_id -> 404 error, message NOT persisted."""

    async def test_send_message_text_only_still_works(self, mock_user):
        """sendMessage without attachments -> existing behavior unchanged."""

    async def test_send_message_exceeds_attachment_limit(self, mock_user):
        """sendMessage with >10 attachments -> 400 error."""
```

**`tests/test_create_and_parse.py`** (new file):
- Mirror the `sendMessage` attachment tests for the `createAndParse` path:

```python
@pytest.mark.asyncio
class TestCreateAndParseWithAttachments:
    """createAndParseUserMessage must handle attachments identically to sendMessage."""

    async def test_create_parse_with_valid_attachments(self, mock_user, mock_file_uploads_db):
        """createAndParse with top-level attachments -> message persisted with
        server-resolved metadata, memory request includes attachments."""

    async def test_create_parse_text_only_unchanged(self, mock_user):
        """createAndParse without attachments -> existing behavior unchanged."""

    async def test_create_parse_invalid_file_id_returns_404(self, mock_user):
        """createAndParse with nonexistent file_id -> 404, message NOT persisted."""

    async def test_create_parse_exceeds_attachment_limit(self, mock_user):
        """createAndParse with >MAX_ATTACHMENTS_PER_MESSAGE -> 400 error."""

    async def test_create_parse_memory_receives_attachments(
        self, mock_user, mock_file_uploads_db, mock_memory_service
    ):
        """After attachment resolution, RoomCenterMemoryRequest.attachments
        contains the resolved UserAttachment list."""
        # ... exercise createAndParse ...
        call_args = mock_memory_service.initialize_or_update_room_memory.call_args
        assert call_args.args[0].attachments is not None
        assert len(call_args.args[0].attachments) == 1
```

**`tests/test_dual_source_attachments.py`** (new file):
- Tests for the dual-source merge strategy and server-side re-resolution:

```python
@pytest.mark.asyncio
class TestDualSourceAttachmentMerge:
    """Verify merge semantics: both sources accepted, deduplicated by file_id,
    all metadata re-resolved server-side.

    Test data uses raw request_data dicts (HTTP payload level). The API handler
    (section 5.4) extracts inline file_ids from message_content.attachments,
    pops the field from the raw dict, and passes inline_file_ids to the Pydantic
    model. Tests exercise this full handler path."""

    # --- Source combinations ---

    async def test_top_level_only(self, mock_user, mock_db, mock_file_uploads_db):
        """Top-level attachments only (no inline) -> resolved from DB."""
        request_data = {
            "room_id": "room-1",
            "message": {
                "room_id": "room-1", "message_id": "msg-1",
                "message_type": "user",
                "message_content": {"message_text": "text"},
            },
            "attachments": [{"file_id": "f1"}],
        }
        # ... call handler ...
        persisted = mock_db.add_room_user_message.call_args[0][0]
        assert len(persisted.message_content.attachments) == 1
        assert persisted.message_content.attachments[0].file_id == "f1"
        # Metadata from server, not client
        assert persisted.message_content.attachments[0].mime_type == "image/png"

    async def test_inline_only(self, mock_user, mock_db, mock_file_uploads_db):
        """Inline attachments only (no top-level) -> file_id extracted,
        metadata re-resolved from DB. Client-provided metadata discarded."""
        request_data = {
            "room_id": "room-1",
            "message": {
                "room_id": "room-1", "message_id": "msg-2",
                "message_type": "user",
                "message_content": {
                    "message_text": "text",
                    "attachments": [{"file_id": "f1", "s3_key": "fake",
                                     "mime_type": "image/svg+xml",
                                     "file_name": "evil.svg", "size_bytes": 999}],
                },
            },
            # No top-level attachments
        }
        # ... call handler ...
        persisted = mock_db.add_room_user_message.call_args[0][0]
        assert len(persisted.message_content.attachments) == 1
        att = persisted.message_content.attachments[0]
        assert att.file_id == "f1"
        assert att.mime_type != "image/svg+xml"  # server metadata, not client
        assert att.s3_key != "fake"               # server metadata, not client

    async def test_inline_without_s3_key_accepted(self, mock_user, mock_db, mock_file_uploads_db):
        """Frontend stuffs upload response (file_id + file_url, NO s3_key) into
        inline attachments. Must not fail Pydantic validation — file_id is
        extracted from raw dict before model construction (section 5.4)."""
        request_data = {
            "room_id": "room-1",
            "message": {
                "room_id": "room-1", "message_id": "msg-2b",
                "message_type": "user",
                "message_content": {
                    "message_text": "text",
                    "attachments": [{"file_id": "f1",
                                     "file_url": "https://s3.example.com/presigned",
                                     "mime_type": "image/png",
                                     "file_name": "photo.png", "size_bytes": 1024}],
                },
            },
        }
        # ... call handler — must NOT raise ValidationError ...
        persisted = mock_db.add_room_user_message.call_args[0][0]
        assert len(persisted.message_content.attachments) == 1
        assert persisted.message_content.attachments[0].file_id == "f1"
        # s3_key comes from server, not client (client didn't even provide one)
        assert persisted.message_content.attachments[0].s3_key.startswith("uploads/")

    async def test_both_sources_merged(self, mock_user, mock_db, mock_file_uploads_db):
        """Both top-level and inline present, different file_ids -> merged."""
        request_data = {
            "room_id": "room-1",
            "message": {
                "room_id": "room-1", "message_id": "msg-3",
                "message_type": "user",
                "message_content": {
                    "message_text": "text",
                    "attachments": [{"file_id": "f2", "s3_key": "x",
                                     "mime_type": "x", "file_name": "x",
                                     "size_bytes": 0}],
                },
            },
            "attachments": [{"file_id": "f1"}],
        }
        # ... call handler ...
        persisted = mock_db.add_room_user_message.call_args[0][0]
        assert len(persisted.message_content.attachments) == 2
        ids = {a.file_id for a in persisted.message_content.attachments}
        assert ids == {"f1", "f2"}

    async def test_neither_source_is_text_only(self, mock_user, mock_db):
        """Neither source provides attachments -> text-only message."""
        # ... standard text message, no attachments field at all ...
        persisted = mock_db.add_room_user_message.call_args[0][0]
        assert persisted.message_content.attachments is None
        assert persisted.message_content.content_summary is None

    # --- Deduplication ---

    async def test_duplicate_file_id_across_sources_deduplicated(
        self, mock_user, mock_db, mock_file_uploads_db
    ):
        """Same file_id in both top-level and inline -> single attachment."""
        request_data = {
            "room_id": "room-1",
            "message": {
                "room_id": "room-1", "message_id": "msg-4",
                "message_type": "user",
                "message_content": {
                    "message_text": "text",
                    "attachments": [{"file_id": "f1", "s3_key": "x",
                                     "mime_type": "x", "file_name": "x",
                                     "size_bytes": 0}],
                },
            },
            "attachments": [{"file_id": "f1"}],
        }
        persisted = mock_db.add_room_user_message.call_args[0][0]
        assert len(persisted.message_content.attachments) == 1

    async def test_duplicate_file_id_within_top_level_deduplicated(
        self, mock_user, mock_file_uploads_db
    ):
        """Same file_id repeated in top-level -> deduplicated to one."""
        request_data = {
            # ...
            "attachments": [{"file_id": "f1"}, {"file_id": "f1"}],
        }
        # Verify 1 attachment (not 2)

    # --- Security / validation ---

    async def test_client_metadata_never_trusted(
        self, mock_user, mock_db, mock_file_uploads_db
    ):
        """Inline attachment has fake metadata -> all fields come from DB."""
        # Inline provides mime_type="application/exe", file_name="malware.exe"
        # Server resolves from DB -> actual mime_type and file_name used

    async def test_cross_room_file_id_rejected(self, mock_user, mock_db):
        """file_id belongs to a different room -> 404 (room_id filter on query)."""

    async def test_merged_count_exceeds_limit(self, mock_user):
        """Top-level has 6, inline has 6 (4 overlap) -> merged = 8, under limit.
        Top-level has 6, inline has 6 (0 overlap) -> merged = 12, over limit -> 400."""

    async def test_raw_refs_exceed_pre_dedup_limit_top_level(self, mock_user):
        """Top-level sends 51 refs (all same file_id) -> error response (success=False,
        status_code=400) before any per-item iteration or DB work.
        Verifies MAX_ATTACHMENT_REFS_PER_REQUEST."""
        request_data = {
            "room_id": "room-1",
            "message": {
                "room_id": "room-1", "message_id": "msg-dos",
                "message_type": "user",
                "message_content": {"message_text": "text"},
            },
            "attachments": [{"file_id": "f1"}] * 51,
        }
        # ... call handler -> expect success=False, status_code=400,
        # error contains "Too many attachment references"
        mock_db.file_uploads_collection.find_one.assert_not_called()  # no DB work

    async def test_raw_refs_exceed_pre_dedup_limit_combined(self, mock_user):
        """Top-level 30 + inline 25 = 55 raw refs -> error response (success=False,
        status_code=400) before per-item iteration.
        Verifies both sources count toward the guard."""
        request_data = {
            "room_id": "room-1",
            "message": {
                "room_id": "room-1", "message_id": "msg-dos2",
                "message_type": "user",
                "message_content": {
                    "message_text": "text",
                    "attachments": [{"file_id": f"inline-{i}"} for i in range(25)],
                },
            },
            "attachments": [{"file_id": f"top-{i}"} for i in range(30)],
        }
        # ... call handler -> expect success=False, status_code=400
        mock_db.file_uploads_collection.find_one.assert_not_called()  # no DB work

    async def test_raw_refs_at_pre_dedup_limit_accepted(self, mock_user, mock_file_uploads_db):
        """Exactly 50 raw refs -> passes pre-dedup guard, proceeds to dedup + resolve."""
        request_data = {
            "room_id": "room-1",
            "message": {
                "room_id": "room-1", "message_id": "msg-edge",
                "message_type": "user",
                "message_content": {"message_text": "text"},
            },
            "attachments": [{"file_id": "f1"}] * 50,
        }
        # ... call handler -> passes pre-dedup guard (50 == limit), dedup yields 1 unique,
        # proceeds to _resolve_attachments() which succeeds (1 <= 10) ...

    async def test_create_parse_raw_refs_exceed_pre_dedup_limit(self, mock_user):
        """createAndParse path applies the same pre-dedup guard.
        Same payload as test_raw_refs_exceed_pre_dedup_limit_top_level
        -> expect success=False, status_code=400."""

    async def test_content_summary_always_server_generated(
        self, mock_user, mock_db, mock_file_uploads_db
    ):
        """Client provides content_summary in message -> overwritten by server."""
        request_data = {
            "room_id": "room-1",
            "message": {
                "room_id": "room-1", "message_id": "msg-5",
                "message_type": "user",
                "message_content": {
                    "message_text": "text",
                    "content_summary": {"has_images": True, "attachment_count": 99},
                },
            },
            "attachments": [{"file_id": "f1"}],
        }
        persisted = mock_db.add_room_user_message.call_args[0][0]
        assert persisted.message_content.content_summary["attachment_count"] == 1

    # --- Both entry points ---

    async def test_create_parse_uses_same_merge_logic(self, mock_user, mock_db):
        """createAndParse applies identical merge + resolve strategy."""
```

**`tests/test_file_url_exclusion.py`** (new file):
- Verify `file_url` is never persisted to MongoDB:

```python
@pytest.mark.asyncio
class TestFileUrlPersistenceExclusion:
    async def test_file_url_stripped_from_user_message_dump(self):
        """model_dump of RoomUserMessage with attachments -> no file_url in output."""
        att = UserAttachment(
            file_id="f1", s3_key="k1", mime_type="image/png",
            file_name="img.png", size_bytes=1024,
        )
        msg = RoomUserMessage(
            room_id="r1", message_id="m1", message_type="user",
            message_content=MessageContent(
                message_text="hi", attachments=[att]
            ),
        )
        doc = msg.model_dump(mode="json")
        for a in doc["message_content"]["attachments"]:
            assert "file_url" not in a or a["file_url"] is None

    async def test_db_write_strips_file_url_via_helper(self, mock_db):
        """_strip_file_urls removes file_url key from serialized doc (insert path)."""
        doc = {"message_content": {"attachments": [
            {"file_id": "f1", "s3_key": "k", "file_url": "https://leaked.com"}
        ]}}
        _strip_file_urls(doc)
        assert "file_url" not in doc["message_content"]["attachments"][0]

    async def test_db_update_strips_file_url_via_helper(self, mock_db):
        """_strip_file_urls handles $set-wrapped docs (update path)."""
        doc = {"$set": {"message_content": {"attachments": [
            {"file_id": "f1", "s3_key": "k", "file_url": "https://leaked.com"}
        ]}}}
        _strip_file_urls(doc)
        assert "file_url" not in doc["$set"]["message_content"]["attachments"][0]
```

**`tests/test_task_notification.py`** (new or extend existing):
- Webhook/task_update path with non-text artifact:

```python
@pytest.mark.asyncio
class TestWebhookMultimodal:
    async def test_webhook_completed_with_file_artifact(self):
        """Webhook for completed task with FilePart artifact -> SSE task_update
        includes both text content and non-text parts."""

    async def test_webhook_completed_text_only_unchanged(self):
        """Webhook for completed task with text-only artifacts -> existing behavior."""

    async def test_task_notification_parts_forwarded_to_sse(self):
        """Non-text parts from artifacts -> SSE send_task_update receives parts param."""
```

**`tests/test_room_services.py`** (extend existing):
- Room deletion cascade:

```python
@pytest.mark.asyncio
class TestRoomDeletionCascade:
    async def test_delete_room_cascades_to_messages(self):
        """Room deletion -> user messages, agent messages deleted from MongoDB."""

    async def test_delete_room_cascades_to_s3(self, mock_s3_service):
        """Room deletion -> S3 delete_prefix called for uploads/ and artifacts/."""

    async def test_delete_room_s3_failure_still_succeeds(self, mock_s3_service):
        """S3 cleanup failure -> room still deleted (best-effort S3 cleanup)."""
```

---

## 8. Security Considerations

| Risk | Mitigation |
|------|-----------|
| Malicious file upload (XSS via SVG, zip bombs) | Server-side MIME allowlist (no SVG), file size limit (10MB), magic byte validation |
| Presigned URL leakage | Short TTL (1 hour), room-scoped access check before URL generation |
| Base64 payload size in SSE | Convert inline bytes to S3 URIs before SSE broadcast (section 6.6) |
| Content-type spoofing | Validate actual file content (magic bytes) against declared MIME type |
| Memory exhaustion from large uploads | File size limit (10MB) enforced before S3 upload; `aioboto3` async upload keeps event loop non-blocking |
| S3 bucket access | IAM role scoped to single bucket, no public access, presigned URLs only |
| SSRF via URL storage type | `StorageType.URL` remains `NotImplementedError` (blocked per `CONTEXT_MEMORY_SYSTEM_DESIGN.md` section 6.8) |
| Unbounded agent S3 writes | `MAX_INLINE_CONVERSIONS_PER_MESSAGE = 20` cap on base64-to-S3 conversions per message; excess parts left as-is |
| Unbounded attachments per message | Two-tier limit: `MAX_ATTACHMENT_REFS_PER_REQUEST = 50` (pre-dedup DoS guard at API layer) + `MAX_ATTACHMENTS_PER_MESSAGE = 10` (semantic limit at `_resolve_attachments()` after merge + dedup) |
| Cross-room file reference | Attachment resolution queries `file_uploads` with both `file_id` AND `room_id` (see section 5.5) |

### 8.1 S3 Lifecycle Management

**Room/message deletion**: The current `delete_room_by_room_id()` (line 441 of
`room_services.py`) only deletes the `rooms` document. The underlying
`mongodb.delete_room_by_room_id()` (line 614) also only deletes the room doc
-- it does **not** cascade to messages, memory, or file_uploads.

Room deletion must cascade to DB + S3. **Order: DB first, then S3** (DB failures
are more likely to need rollback; S3 orphans are cleaned up by the orphan job):

```python
# In room_services.delete_room_by_room_id():
room_id = request.room_id

# 1. Delete DB records (messages, memory, file_uploads metadata)
await mongodb.room_user_messages_collection.delete_many({"room_id": room_id})
await mongodb.room_agent_messages_collection.delete_many({"room_id": room_id})
await mongodb.room_memories_collection.delete_many({"room_id": room_id})
await mongodb.file_uploads_collection.delete_many({"room_id": room_id})

# 2. Delete the room document itself
success = await self.database_service.delete_room_by_room_id(room_id)

# 3. Best-effort S3 cleanup (orphan job catches any failures)
try:
    await s3_service.delete_prefix(f"uploads/{room_id}/")
    await s3_service.delete_prefix(f"artifacts/{room_id}/")
except Exception:
    logger.warning("S3 cleanup failed for room %s; orphan job will retry", room_id)
```

Add `delete_prefix()` to `S3Service`:

```python
async def delete_prefix(self, prefix: str) -> int:
    """Delete all objects under an S3 prefix. Returns count deleted."""
    async with self._session.resource("s3", region_name=self._region) as s3:
        bucket = await s3.Bucket(self._bucket)
        deleted = 0
        async for obj in bucket.objects.filter(Prefix=prefix):
            await obj.delete()
            deleted += 1
        return deleted
```

For individual message deletion, delete the message's S3 artifacts:

```python
# In delete_room_user_message_by_message_id:
if message.message_content and message.message_content.attachments:
    for att in message.message_content.attachments:
        await s3_service.delete_file(att.s3_key)
```

**Orphaned upload cleanup**: Background job to clean up files uploaded but never
attached to a message. Run daily via the existing job scheduler:

```python
async def cleanup_orphaned_uploads(max_age_hours: int = 24) -> int:
    """Delete file_uploads records (and their S3 objects) that are older
    than max_age_hours and not referenced by any message attachment.

    Strategy:
    1. Query file_uploads where uploaded_at < (now - max_age_hours)
    2. For each, check if file_id appears in any room message attachment
    3. If not referenced, delete from S3 and remove metadata from MongoDB
    """
    cutoff = utcnow() - timedelta(hours=max_age_hours)
    cursor = mongodb.file_uploads_collection.find({"uploaded_at": {"$lt": cutoff}})
    deleted = 0
    async for doc in cursor:
        # Check if referenced in any message
        ref = await mongodb.room_user_messages_collection.find_one(
            {"message_content.attachments.file_id": doc["file_id"]}
        )
        if ref is None:
            await s3_service.delete_file(doc["s3_key"])
            await mongodb.file_uploads_collection.delete_one({"_id": doc["_id"]})
            deleted += 1
            logger.info("Cleaned up orphaned upload: %s", doc["file_id"])
    return deleted
```

Register as a periodic job in the existing job scheduler infrastructure.

---

## 9. Cross-Cutting Concerns

### 9.1 Existing Feature Forward-Compatibility

| Feature | Multi-modal impact | Action |
|---------|-------------------|--------|
| Context Memory System | Text annotation approach (section 5.7) makes attachments visible to all memory consumers with zero schema changes | None |
| Token Budget | Annotation is ~10-15 tokens per attachment, negligible impact on budget | None |
| Compaction | Annotation is part of `ConversationTurn.content` string, compaction works as-is | None |
| HITL | HITL replies remain text-only. Future: add `attachments` to `respondToHitl` API | Note in HITL doc |
| Supervisor V2 | Supervisor sees attachment annotations in context and can route to vision-capable agents | None |
| Task Retry | `retryMessage` should forward `userEntity.attachments` to `sendUserMessage` | Frontend change (see `MULTIMODAL_SUPPORT_DESIGN.md` section 4.3) |
| Message Pagination | Paginated messages may include attachments. Presigned URL generation happens at read time (section 5.6) | None |
| Token Streaming | Token streaming is text-only. Non-text content arrives via `artifact_update`. No conflict. | None |

### 9.2 Performance Characteristics

| Operation | Concern | Mitigation |
|-----------|---------|-----------|
| File upload | Could block event loop with synchronous S3 | `aioboto3` for async S3 operations |
| Presigned URL generation | Per-attachment latency on message retrieval | In-memory cache (30min TTL), batch generation |
| Base64-to-S3 conversion | CPU + network cost during streaming | Only triggers for inline bytes (rare); S3 upload is async |
| SSE payload size | Large artifacts inflate SSE stream | Base64-to-S3 conversion caps SSE payload size |
| MongoDB document size | Attachments add ~200 bytes per file to message doc | Well under 16MB BSON limit even with 50 attachments |

### 9.3 Dual Type System (`a2a.types` vs `common/types.py`)

The codebase has **two parallel type systems** for A2A types:

| | SDK (`a2a.types`) | Local (`common/types.py`) |
|---|---|---|
| Field naming | `snake_case` (Python-native) | `camelCase` (JSON-native) |
| `AgentCard` fields | `default_output_modes`, `default_input_modes` | `defaultOutputModes`, `defaultInputModes` |
| `MessageSendConfiguration` | `accepted_output_modes` | N/A (not duplicated) |
| `FilePart` construction | `FilePart(file=FileWithUri(uri=..., mime_type=...))` | `FilePart(file=FileContent(uri=..., mimeType=...))` |
| Used by | `a2a_service.py`, `modules/ResponseProcessor.py` | `services/room_services.py`, tests |

**Rule for new multimodal code**: Always use the SDK types (`from a2a.types import ...`)
with snake_case field names. The local `FileContent` class in `common/types.py` should
NOT be used for constructing `FilePart` -- use `FileWithUri` or `FileWithBytes` from
the SDK instead. The SDK supports camelCase aliases via `validate_by_alias=True` but
these are deprecated and will emit warnings.

**Action**: At implementation time, audit each import to confirm which type is in
scope. Using `getattr(agent_card, 'defaultOutputModes', ...)` with SDK `AgentCard`
will silently return the default because the actual Python attribute is
`default_output_modes`.

---

## 10. Implementation Order and Effort

```
Phase 1 (S3 Storage)        ~2-3 days
  - S3Service + aioboto3 (incl. delete_prefix)
  - FileUploadService + validation + compensating delete
  - POST /files/upload endpoint
  - file_uploads collection + indexes
  - .env + config/settings.py configuration
  - Orphaned upload cleanup job
  - S3 cleanup in room/message deletion handlers

Phase 2 (Message Model)     ~2-3 days
  - UserAttachment model
  - MessageContent extensions
  - RoomCenterUserMessageRequest extension
  - API endpoint handler (api/room_center.py) attachment extraction
  - Message creation flow with attachment resolution (room_services.py)
  - Memory service integration (thread attachments, build_turn_content)
  - RoomMessageCenter attachment propagation to supervisor
  - Message retrieval with batch presigned URLs
  - SSE agent_response parts field

Phase 3 (A2A Negotiation)   ~3-4 days
  - _resolve_accepted_modes() (with SDK snake_case field names)
  - Replace 4 hardcoded accepted_output_modes
  - Unified extract_parts() + backward-compat wrappers
  - _build_message_parts() with FilePart (covers create_task_for_agent,
    create_task_for_agents_group, _generate_agent_message_content)
  - Agent response non-text handling (streaming message chunks,
    sync response parsing, finalize streaming SSE)
  - Base64-to-S3 conversion in artifact processing
  - ContentStorageService S3 expansion

Phase 4 (Tests)             ~2-3 days
  - Part extraction test suite
  - File upload test matrix
  - Mode negotiation parametrized tests
  - Turn annotation and content summary tests
  - Message retrieval and presigned URL tests
  - Error handling test suite
  - conftest.py fixtures and PATCH dict
  - End-to-end integration test
```

**Total estimate**: ~10-14 days

---

## 11. Files Summary

### New Files

| File | Phase | Purpose |
|------|-------|---------|
| `services/s3_service.py` | 1 | Async S3 wrapper (upload, presigned URLs, delete) |
| `services/file_upload_service.py` | 1 | Upload orchestration (validation, S3, metadata) |
| `api/files.py` | 1 | `POST /files/upload` endpoint |
| `models/file_upload.py` | 1 | `FileUploadMetadata`, `FileUploadResponse`, constants |
| `tests/test_multimodal_part_extraction.py` | 4 | Part extraction test suite |
| `tests/test_file_upload.py` | 4 | File upload test matrix |
| `tests/test_mode_negotiation.py` | 4 | Mode negotiation tests |
| `tests/test_multimodal_integration.py` | 4 | End-to-end integration test |
| `tests/test_turn_annotation.py` | 4 | Turn annotation and content summary tests |
| `tests/test_message_retrieval.py` | 4 | Presigned URL injection tests |
| `tests/test_multimodal_errors.py` | 4 | Error handling tests (S3 failures, missing file_id) |
| `tests/test_create_and_parse.py` | 4 | createAndParseUserMessage attachment coverage |
| `tests/test_dual_source_attachments.py` | 4 | Dual-source merge, dedup, inline-only fallback, security validation |
| `tests/test_file_url_exclusion.py` | 4 | Verify file_url never persisted to MongoDB |
| `jobs/cleanup_orphaned_uploads.py` | 1 | Background job: delete uploads not attached to messages after 24h |

### Modified Files

| File | Phase | Changes |
|------|-------|---------|
| `models/room.py` | 2 | Add `UserAttachment`, extend `MessageContent` |
| `models/request.py` | 2 | Add `UserAttachmentRequest` (file_id only), extend `RoomCenterUserMessageRequest` with `attachments` and `inline_file_ids` fields, extend `RoomCenterMemoryRequest` with `attachments` field |
| `api/room_center.py` | 2 | Extract `attachments` and inline `file_id`s from raw request dict (before Pydantic construction) in **both** `send_message` and `create_and_parse_user_message`; pass `inline_file_ids` to `RoomCenterUserMessageRequest` |
| `services/room_services.py` | 1, 2, 3 | S3 cleanup in `delete_room_by_room_id()`, shared `_resolve_attachments(file_ids)` helper, dual-source merge + resolution in both `send_message_to_room()` and `create_and_parse_user_message()`, `_build_message_parts()` with `FilePart`, `_generate_agent_message_content()` update |
| `database/mongodb.py` | 2 | Add `file_uploads_collection` property, add `_strip_file_urls()` helper applied to both insert (`add_room_user_message`) and update (`update_room_user_message_by_message_id`) paths |
| `services/memory_service.py` | 2 | Apply `build_turn_content()` with `request.attachments` from `RoomCenterMemoryRequest` |
| `modules/RoomMessageCenter.py` | 2 | Apply `build_turn_content()` in `_process_supervisor_v2()`, thread attachments to agent dispatch |
| `services/a2a_service.py` | 3 | `_resolve_accepted_modes()`, replace 4x hardcoded modes (use snake_case SDK field names) |
| `common/utils/a2a_helpers.py` | 3 | `extract_parts()`, `extract_parts_from_artifacts()`, wrapper updates |
| `services/sse_services.py` | 2, 3 | `send_agent_response` `parts` parameter, `send_task_update` `parts` parameter |
| `api/webhooks.py` | 3 | Use `extract_parts_from_artifacts()` instead of `extract_text_from_artifacts()`, thread non-text parts to task notification |
| `services/task_notification_service.py` | 3 | Thread non-text parts from artifacts to `send_task_update()` SSE |
| `modules/ResponseProcessor.py` | 3 | `_convert_inline_bytes_to_s3()`, `_handle_stream_message_chunk` non-text extraction, `_parse_sync_fallback_response` non-text extraction, `_finalize_streaming` non-text SSE, `MessageStreamingState.non_text_parts` |
| `services/content_storage_service.py` | 3 | S3 expansion (replace `NotImplementedError`) |
| `main.py` | 1 | Register files router, instantiate S3/upload services as module-level singletons |
| `pyproject.toml` | 1 | Add `aioboto3` dependency |
| `.env.example` | 1 | Add S3 configuration variables |
| `config/settings.py` | 1 | Add S3 settings fields (`s3_bucket_name`, `s3_region`, `s3_presigned_url_ttl`, `max_file_size_mb`, AWS credentials) |
| `tests/conftest.py` | 4 | Add PATCH dict entries for files/S3 services, `mock_s3_service` and `sample_file_upload_metadata` fixtures |
| `tests/test_compaction_service.py` | 4 | Replace `NotImplementedError` assertion with S3 download tests |
| `tests/test_api_room_center.py` | 4 | Add attachment variant tests for `sendMessage` |
| `tests/test_room_services.py` | 4 | Add room deletion cascade tests (DB + S3) |

---

## 12. Key Decisions

| Decision | Rationale |
|----------|-----------|
| 4 phases, each independently shippable | Reduces risk; Phase 1 is infra-only with no user-visible impact |
| `aioboto3` over synchronous `boto3` | Maintains fully-async pattern (matching `motor`, `httpx`); avoids blocking event loop |
| Store `s3_key` in DB, generate presigned URLs at read time | URLs never go stale in database; short TTL for security |
| Presigned URL cache (30min TTL) | Avoids regeneration cost on rapid page refreshes |
| Text annotation for context assembly (no schema change to `ConversationTurn`) | All downstream consumers (supervisor, memory, search, compaction) work without modification |
| `content_summary` on `MessageContent` | Enables efficient queries by media type without scanning nested artifacts |
| `PLATFORM_SUPPORTED_MODES` constant for mode negotiation | Single source of truth; easy to expand as frontend capabilities grow |
| Unified `extract_parts()` function | DRYs up part extraction; backward-compat wrappers preserve existing callers |
| Base64-to-S3 conversion before SSE broadcast | Protects SSE stream from multi-MB payloads regardless of agent behavior |
| `S3Service` shared by `FileUploadService` and `ContentStorageService` | One S3 client for all binary storage needs; avoids duplicate config/connections |
| `UserAttachment` separate from `ArtifactData` | Different sources (user vs agent), different storage, different rendering |
| Compensating S3 delete on MongoDB failure | Non-atomic S3+MongoDB write requires explicit rollback to prevent orphaned S3 objects |
| `MAX_ATTACHMENTS_PER_MESSAGE = 10` | Semantic limit on unique attachments per message; enforced in `_resolve_attachments()` after merge + dedup |
| `MAX_ATTACHMENT_REFS_PER_REQUEST = 50` | Pre-dedup DoS guard at API layer; rejects requests with absurd raw ref counts before any iteration/DB work. Set at 5x the semantic limit to allow legitimate duplicates while blocking abuse |
| `MAX_INLINE_CONVERSIONS_PER_MESSAGE = 20` | Caps S3 writes from misbehaving agents; excess base64 parts left as-is |
| Cross-room file reference blocked | Attachment resolution queries by `file_id` AND `room_id`; enforces room isolation |
| Background orphan cleanup (24h TTL) | Files uploaded but never attached are deleted daily; prevents unbounded S3 growth |
| S3 prefix cleanup on room deletion | Deletes all uploads and artifacts under `{room_id}/` when room is deleted |

---

## 13. Out of Scope

- Audio/video recording in chat (requires media capture APIs)
- Real-time collaborative editing of DataPart content
- File versioning or edit history
- Agent-to-agent file transfer (handled transparently by A2A protocol)
- OCR or image understanding on uploaded files (agent capability, not platform)
- CDN layer for frequently accessed files (CloudFront can be added later)
- `StorageType.URL` expansion (blocked by SSRF risk per `CONTEXT_MEMORY_SYSTEM_DESIGN.md`)
