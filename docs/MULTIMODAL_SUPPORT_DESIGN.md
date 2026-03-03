# Multi-Modality Support Design

> **Status: Not Started** — Design approved, pending implementation. 4 independent phases.

**Depends on**: None (phases are independently shippable)
**Relationship to other docs**:
- `ARTIFACT_RENDERING_DESIGN.md` is Phase 1 Output — rendering A2A artifact parts
- `TOKEN_STREAMING_DESIGN.md` — token streaming remains text-only; no changes needed
- `HITL_FRONTEND_DESIGN.md` — HITL replies are text-only in Phase 1; image/file replies in Phase 3

---

## 1. Problem Statement

The entire Hybro stack is **text-only** today. Both the backend and frontend treat all
message content as plain strings. However:

1. **A2A agents already declare multi-modal capabilities.** Agent cards include
   `defaultOutputModes: ["text", "image"]` and `defaultInputModes: ["text", "file"]`,
   but the backend hardcodes `acceptedOutputModes=["text/plain"]`, actively blocking
   non-text agent output.

2. **The A2A SDK defines `FilePart`, `DataPart`, and `TextPart`**, all discriminated by
   `kind`. The backend imports all three but only ever instantiates `TextPart`. The
   frontend re-exports all three from `@a2a-js/sdk` but never uses `FilePart` or
   `DataPart`.

3. **Users cannot send images or files.** The chat input is a `contentEditable` div that
   strips pasted images to `text/plain`. There is no file upload endpoint, no attachment
   button, and no drag-and-drop handler.

4. **Agent artifacts containing non-text parts are silently discarded.** Both
   `extract_text_from_artifacts()` (backend) and `extractTaskContent()` (frontend) only
   read `.text` fields from parts, ignoring `FilePart` and `DataPart` entirely.

This design ensures all current and planned features (`ARTIFACT_RENDERING_DESIGN.md`,
`HITL_FRONTEND_DESIGN.md`, `TOKEN_STREAMING_DESIGN.md`) are forward-compatible with
multi-modal content.

---

## 2. Current State Audit

### 2.1 Backend

| Layer | Current | Multi-modal gap |
|-------|---------|-----------------|
| A2A send configuration | `acceptedOutputModes=["text/plain"]` hardcoded in 4 places (`a2a_service.py` lines 314, 567, 620, 684) | Blocks agents from returning images/files |
| User message input | `user_input: str` (JSON body) | No file upload, no multipart/form-data |
| Message storage | `MessageContent.message_text: str` + `message_task: Task` | No fields for file refs, binary metadata |
| Artifact handling | `extract_text_from_artifacts()` reads only `.text` | `FilePart`/`DataPart` silently dropped |
| SSE events | `content: str` in `agent_response`, `task_update` | No structured parts in content events |
| SSE `artifact_update` | Passes raw `artifact` object through | **Partially works** — if an agent returned a `FilePart` artifact, it would reach the frontend |
| Binary storage | S3 designed in `CONTEXT_MEMORY_SYSTEM_DESIGN.md §6.8` but raises `NotImplementedError` | No binary storage backend |
| Memory/context | `ContentType` enum has `TEXT`, `TOOL_RESULT`, `AGENT_RESPONSE`; future placeholders for `IMAGE`, `FILE`, `VIDEO`, `AUDIO` | Only text compaction implemented |
| Part types | `Part = TextPart | FilePart | DataPart` (from `a2a.types`) | `FilePart`/`DataPart` never instantiated |

### 2.2 Frontend

| Layer | Current | Multi-modal gap |
|-------|---------|-----------------|
| `MessageEntity.content` | `string` | No `parts[]`, `attachments[]`, or `files[]` field |
| `IncomingMessage.content` | `string` | Same |
| SSE `data.content` | `string` | No structured content |
| Chat input | `contentEditable` div, strips pasted images | No `<input type="file">`, no drag-and-drop, no attachment button |
| `SendMessage` API | `user_input: string` | No multipart/form-data or base64 payloads |
| Message bubble rendering | `MarkdownContent` (agent) / `LinkifiedContent` (user) | No image preview, file card, audio player |
| `convertApiMessageToIncoming` | Reads only `message_content.message_text` | Ignores non-text artifact parts |
| `extractTaskContent` | Reads only `part.text` from artifacts | `FilePart`/`DataPart` silently discarded |
| A2A SDK types | `FilePart`, `DataPart`, `FileWithBytes`, `FileWithUri` imported but unused | Dead re-exports |

### 2.3 What Already Works (Partially)

1. **Artifact SSE passthrough**: `ResponseProcessor._handle_stream_artifact_update()`
   forwards the raw artifact object via SSE, including any `FilePart`/`DataPart` it
   contains. If the backend stopped blocking non-text output modes, artifacts with
   images would reach the frontend.

2. **A2A SDK types are available**: Both codebases have `Part`, `TextPart`, `FilePart`,
   `DataPart` in their type systems. No new type imports needed.

3. **Agent cards declare modalities**: `defaultOutputModes` and `defaultInputModes`
   are stored on agent cards and accessible to both backend and frontend.

---

## 3. Phased Design

Multi-modality is a large surface area. This design is split into 4 phases, each
independently shippable and testable.

```
Phase 1: Agent Output Rendering        (frontend-heavy, minimal backend)
    ↓
Phase 2: Dynamic Output Mode Negotiation (backend-only)
    ↓
Phase 3: User File Input                (full-stack)
    ↓
Phase 4: Binary Storage & Context       (backend-heavy)
```

### 3.1 Phase 1 — Agent Output Rendering (= `ARTIFACT_RENDERING_DESIGN.md`)

**Goal**: Render `FilePart` (images, files) and `DataPart` (JSON) in agent messages.

**Why first**: Even with `acceptedOutputModes=["text/plain"]`, some agents may include
non-text parts in artifacts (the spec doesn't guarantee compliance). More importantly,
this phase can be tested with mock SSE events immediately.

**Scope**: This is exactly `ARTIFACT_RENDERING_DESIGN.md`. No changes needed to that
doc — it already covers:
- `ArtifactData` type on `MessageEntity`
- `artifact_update` SSE handler
- `mergeArtifacts` in `upsert.ts`
- `PartRenderer` with `part.kind` discriminator (`'text'`, `'file'`, `'data'`)
- `ArtifactList` and `ArtifactRenderer` components
- `next/dynamic` lazy loading for `PartRenderer`

**Forward-compatibility check**: Phase 1 renders artifacts attached to agent task
messages. Phase 3 (user file input) will need to render user-sent images inline in
user bubbles — a different display path. The `PartRenderer` component from Phase 1
can be reused for this, but user bubbles will need their own integration point (not
through `ArtifactList`). This is fine — `PartRenderer` is a leaf component with no
assumptions about where it's rendered.

**No backend changes required.**

### 3.2 Phase 2 — Dynamic Output Mode Negotiation (Backend)

**Goal**: Stop hardcoding `acceptedOutputModes=["text/plain"]`. Instead, negotiate
based on the agent's `defaultOutputModes` and the room's configured capabilities.

**Files to modify (backend only)**:

#### `services/a2a_service.py` — Dynamic `acceptedOutputModes`

Replace the 4 hardcoded `acceptedOutputModes=["text/plain"]` calls:

```python
# Before (4 locations: lines 314, 567, 620, 684):
configuration=MessageSendConfiguration(acceptedOutputModes=["text/plain"])

# After:
accepted_modes = self._resolve_accepted_modes(agent_card)
configuration=MessageSendConfiguration(acceptedOutputModes=accepted_modes)
```

New method:

```python
# A2A content types the Hybro platform can currently render.
# Expand this set as frontend rendering capabilities are added.
PLATFORM_SUPPORTED_MODES = {"text/plain", "image/png", "image/jpeg",
                            "image/gif", "image/webp", "application/json"}

def _resolve_accepted_modes(self, agent_card) -> list[str]:
    """Intersect agent's output modes with platform capabilities."""
    agent_modes = set(getattr(agent_card, 'defaultOutputModes', ['text']))

    # Map shorthand A2A modes to MIME types
    mode_to_mime = {
        'text': 'text/plain',
        'image': 'image/png',
        'json': 'application/json',
        'form': 'text/plain',       # forms rendered as text for now
        'markdown': 'text/plain',   # markdown is text
    }
    agent_mime_modes = set()
    for mode in agent_modes:
        if '/' in mode:
            agent_mime_modes.add(mode)
        elif mode in mode_to_mime:
            agent_mime_modes.add(mode_to_mime[mode])
        else:
            agent_mime_modes.add('text/plain')

    accepted = agent_mime_modes & PLATFORM_SUPPORTED_MODES
    if not accepted:
        accepted = {'text/plain'}
    return list(accepted)
```

**Key decision**: `PLATFORM_SUPPORTED_MODES` is a **backend constant** that tracks what
the frontend can render. When Phase 1 (artifact rendering) ships, add image MIME types.
When Phase 4 (binary storage) ships, add more file types.

#### `common/utils/a2a_helpers.py` — Handle non-text artifact parts

Update `extract_text_from_artifacts()` to not silently discard non-text parts:

```python
def extract_content_from_artifacts(artifacts: list) -> dict:
    """Extract text and non-text content from A2A artifacts.
    
    Returns:
        {
            'text': str | None,
            'parts': list[dict],   # non-text parts preserved as dicts
        }
    """
    texts = []
    non_text_parts = []
    for artifact in artifacts:
        if not artifact.parts:
            continue
        for part in artifact.parts:
            if hasattr(part, "text") and part.text:
                texts.append(part.text)
            elif hasattr(part, "root"):
                root = part.root
                if hasattr(root, "text") and root.text:
                    texts.append(root.text)
                else:
                    non_text_parts.append(root.model_dump() if hasattr(root, 'model_dump') else vars(root))
            else:
                non_text_parts.append(part.model_dump() if hasattr(part, 'model_dump') else vars(part))
    return {
        'text': "".join(texts) if texts else None,
        'parts': non_text_parts,
    }
```

Keep the old `extract_text_from_artifacts()` as a wrapper for backward compatibility:

```python
def extract_text_from_artifacts(artifacts: list) -> str | None:
    return extract_content_from_artifacts(artifacts)['text']
```

#### SSE `agent_response` — Include non-text parts

Update `send_agent_response` in `sse_services.py` to optionally include a `parts` field
when non-text content is present:

```python
async def send_agent_response(self, room_id, message_id, agent_id, content,
                               parts=None, **kwargs):
    data = {
        "message_id": message_id,
        "agent_id": agent_id,
        "content": content,         # text content (backward compatible)
        "parts": parts,             # non-text parts (new, optional)
        ...
    }
```

**Frontend changes for Phase 2**: None. The frontend already handles `artifact_update`
events (from Phase 1). The `parts` field on `agent_response` is additive — the existing
handler reads `content` and ignores unknown fields. A small follow-up task can wire
`data.parts` into the message entity if needed.

### 3.3 Phase 3 — User File Input (Full-Stack)

**Goal**: Users can send images and files alongside text messages.

#### 3.3.1 Backend: File Upload Endpoint

New endpoint: `POST /api/v1/files/upload`

```python
@router.post("/upload")
async def upload_file(
    file: UploadFile,
    room_id: str = Form(...),
    user_id: str = Form(...),
):
    # Validate: file size (< 10MB), MIME type (allowlist)
    # Store: S3 or local filesystem (Phase 4 adds S3)
    # Return: { file_id, url, mime_type, file_name, size_bytes }
```

For Phase 3, store files **locally** in a configurable directory with a static file
server, or use presigned S3 URLs if S3 is configured (Phase 4).

Return a `file_url` that the frontend can embed in the message.

#### 3.3.2 Backend: Accept File References in Messages

Extend `RoomCenterUserMessageRequest` to accept file attachments:

```python
class UserMessageAttachment(BaseModel):
    file_id: str
    file_url: str
    mime_type: str
    file_name: str
    size_bytes: int | None = None

class RoomCenterUserMessageRequest(BaseModel):
    # ... existing fields ...
    attachments: list[UserMessageAttachment] | None = None
```

When `attachments` is present, the backend constructs a `Message` with both `TextPart`
and `FilePart`:

```python
parts = [TextPart(text=user_input)]
for att in attachments:
    parts.append(FilePart(file=FileContent(
        uri=att.file_url,
        mimeType=att.mime_type,
        name=att.file_name,
    )))
message = Message(role=Role.user, parts=parts, ...)
```

**Note**: `FileContent` is the backend's local class (`common/types.py` line 16) with
`uri`, `bytes`, `mimeType`, and `name` fields. Do NOT use the frontend SDK's
`FileWithUri` — that is a TypeScript type. The exact `FilePart` constructor may vary
depending on the `a2a` Python SDK version; verify at implementation time.

#### 3.3.3 Frontend: Chat Input Enhancements

**New files**:
- `src/lib/types/attachments.ts` — Shared attachment types (canonical location)
- `src/components/file-attachment-button.tsx` — Paperclip button that opens file picker
- `src/components/attachment-preview.tsx` — Thumbnail previews of pending attachments
- `src/lib/api/files.ts` — `uploadFile(file, roomId)` API client

**Shared types** (`src/lib/types/attachments.ts`):

```typescript
/** Pre-upload state: file selected by user but not yet sent to the server. */
export interface PendingAttachment {
  id: string                   // client-generated UUID for keying previews
  file: File                   // raw File object from input/drag/paste
  previewUrl: string | null    // object URL for image previews, null for non-images
  status: 'pending' | 'uploading' | 'uploaded' | 'error'
  progress?: number            // 0–100 upload progress (optional, for UX)
  error?: string               // upload error message
  uploaded?: AttachmentData    // populated once upload completes
}

/** Post-upload state: file stored on server, ready to send with message. */
export interface AttachmentData {
  fileId: string
  fileUrl: string
  mimeType: string
  fileName: string
  sizeBytes?: number
}
```

All signatures across the doc (`onSubmit`, `sendUserMessage`, `PendingRoomData`,
`retryMessage`) reference these types from `@/lib/types/attachments`.

**Convention**: All frontend types use camelCase per `MessageEntity` convention.
The backend API wire format uses snake_case; conversion happens at the API boundary
(inside `SendMessage`, `uploadFile`, and `convertApiMessageToIncoming`).

**Modify `src/components/room-chat-input.tsx`**:

1. Add state: `attachments: PendingAttachment[]` (file + upload status + preview URL)
2. Add attachment button (Paperclip icon) next to send button
3. Add drag-and-drop handler on the chat input area
4. Update `handlePaste` to detect `image/*` clipboard items and add as attachments
5. Render `AttachmentPreview` strip above the input when attachments are present
6. On submit: pass `attachments` through the existing `onSubmit` callback — do NOT
   upload or call `SendMessage` from the input component

**Important architectural constraint**: `room-chat-input.tsx` is a **UI-only** component.
Its `onSubmit` callback (line 18) passes data up to the page/hook layer. The actual
upload → send → optimistic upsert → placeholder lifecycle → processing state is owned
by `useRoomWebhook.sendUserMessage` (line 703). The input component must NOT call
`SendMessage` or `uploadFile` directly.

**Updated `onSubmit` signature**:

```typescript
// Before:
onSubmit: (message: string, targetGroup?: string, quote?: QuoteData | null) => void

// After:
onSubmit: (
  message: string,
  targetGroup?: string,
  quote?: QuoteData | null,
  attachments?: PendingAttachment[],
) => void
```

**Modify `useRoomWebhook.sendUserMessage`** to accept and handle attachments:

```typescript
const sendUserMessage = useCallback(async (
  userInput: string,
  targetGroup: string = "all_agents",
  quoteData?: QuoteData,
  attachments?: PendingAttachment[],
) => {
  // Step 0: Upload files (if any) before sending message
  let uploadedAttachments: AttachmentData[] | undefined
  if (attachments?.length) {
    uploadedAttachments = await Promise.all(
      attachments.map(att => uploadFile(att.file, roomId, getToken))
    )
  }

  // Step 1: Optimistic upsert (include attachments for immediate rendering)
  msgStoreSend.upsertMessage({
    id: tempMessageId,
    roomId,
    messageType: 'user',
    content: userInput,
    senderName: userName,
    userId,
    timestamp: currentTime,
    attachments: uploadedAttachments,
    targetGroup,
  }, 'optimistic')

  // Step 2: Call SendMessage API with attachments
  await SendMessage(roomId, userInput, getToken, userId, userName,
                    targetGroup, null, null, uploadedAttachments)
  // ... existing placeholder, processing, reconciliation logic ...
}, [...])
```

This keeps the architecture boundary intact: input collects attachments, hook
orchestrates upload + send + lifecycle.

#### 3.3.4 `/c/chat` Flow — Room Creation with Attachments

`/c/chat` also renders `RoomChatInput` (`src/app/c/chat/page.tsx` line 407) with its
own `handleSubmit` that creates a room via `useChatRoomCreation.createAndNavigate`.
Currently this handler is text-only (line 153) and `useChatRoomCreation` persists only
`initialMessage` and `targetGroup` into Zustand pending room data (line 135).

If Phase 3 enables attachments on `RoomChatInput` without updating this path,
attachments would be silently dropped during room creation.

**Required changes**:

1. **`handleSubmit` in `src/app/c/chat/page.tsx`** — accept and forward `attachments`:

```typescript
const handleSubmit = async (
  value: string,
  targetGroup?: string,
  quote?: QuoteData | null,
  attachments?: PendingAttachment[],
) => {
  // ... existing validation ...
  const options = {
    // ... existing options ...
    // Forward targetGroup from the callback, NOT from gm.selectedGroup.
    // room-chat-input.tsx intentionally sets targetGroup to undefined when
    // mentions are present (line 611), so the backend routes by mention
    // instead of by group. Overriding with gm.selectedGroup would break
    // mention-driven routing.
    targetGroup,
  }
  const success = await createAndNavigate(value, options, attachments)
  // ...
}
```

2. **`src/stores/room-ui-store.ts` — extend `PendingRoomData`** to accept attachments:

```typescript
interface PendingRoomData {
  initialMessage: string
  targetGroup?: string
  attachments?: PendingAttachment[]  // Phase 3: forwarded to sendUserMessage on room load
}
```

Without this change, TypeScript will reject the `attachments` property in `setPendingRoomData`
calls below, and the room page will have no typed access to consume them.

3. **`useChatRoomCreation.createAndNavigate`** — accept `attachments` and stash them:

```typescript
const createAndNavigate = useCallback(async (
  userMessage: string,
  options: CreateRoomOptions = {},
  attachments?: PendingAttachment[],
) => {
  const roomId = await createRoomWithMessage(userMessage, options)
  if (roomId) {
    // Include attachments in pending room data so the room page can
    // pick them up and send them with the initial message.
    useRoomUiStore.getState().setPendingRoomData(roomId, {
      initialMessage: userMessage,
      targetGroup: options.targetGroup,
      attachments,
    })
    // ...
  }
}, [...])
```

4. **Room page** (`src/app/c/room/[id]/page.tsx`) — when consuming pending room data,
   pass `attachments` into `sendUserMessage`:

```typescript
const pending = useRoomUiStore.getState().consumePendingRoomData(roomId)
if (pending?.initialMessage) {
  await sendUserMessage(
    pending.initialMessage,
    pending.targetGroup,
    undefined, // no quote
    pending.attachments, // may be undefined (text-only room creation)
  )
}
```

This ensures the `/c/chat` → room creation → initial message pipeline carries
attachments end-to-end without breaking the existing text-only default.

**Modify `SendMessage` API** (`src/lib/api/room.ts`):

```typescript
export async function SendMessage(
  room_id: string,
  user_input: string,
  getToken?: () => Promise<string | null>,
  // ... existing params ...
  attachments?: AttachmentData[],
)
```

The `SendMessage` function accepts camelCase `AttachmentData` and serialises to
snake_case for the backend wire format in the request body:

```typescript
body: JSON.stringify({
  // ... existing fields ...
  attachments: attachments?.map(a => ({
    file_id: a.fileId,
    file_url: a.fileUrl,
    mime_type: a.mimeType,
    file_name: a.fileName,
    size_bytes: a.sizeBytes,
  })),
})
```

**Modify user bubble rendering** (`src/components/message-bubble.tsx`):

When `entity.attachments` is present, render inline previews above the text:
- Images: `<img>` with lightbox on click
- Files: download card with icon, name, size

**Modify `MessageEntity`** (`src/stores/message-store/types.ts`):

```typescript
// ── Attachments (user-sent files/images) ─────────────────
attachments?: AttachmentData[]
```

```typescript
interface AttachmentData {
  fileId: string
  fileUrl: string
  mimeType: string
  fileName: string
  sizeBytes?: number
}
```

This is distinct from `artifacts` (agent output) — attachments are user input, artifacts
are agent output. They render differently and are stored differently.

**Modify `mergeIncoming` and `isNoOpUpdate`** in `upsert.ts`:

```typescript
// mergeIncoming (both branches):
attachments: incoming.attachments,
// existing-entity branch:
attachments: incoming.attachments !== undefined
  ? incoming.attachments : existing.attachments,

// isNoOpUpdate:
existing.attachments === coalesce(incoming.attachments, existing.attachments) &&
```

#### 3.3.5 HITL Multi-Modal Replies (Future Extension)

When Phase 3 is complete, `HITL_FRONTEND_DESIGN.md` can be extended to support file
attachments in HITL replies. The `respondToHitl` API would accept an optional
`attachments` array alongside `user_input`. The `HitlInlineReplyForm` text variant
would gain an attachment button.

This is explicitly out of scope for Phase 3's initial implementation — HITL replies
remain text-only until both HITL and file input are independently stable.

### 3.4 Phase 4 — Binary Storage & Context (Backend)

**Goal**: Implement the S3 binary storage designed in
`CONTEXT_MEMORY_SYSTEM_DESIGN.md §6.8`.

This phase is **backend-only** and does not require frontend changes. It replaces local
file storage (from Phase 3) with S3, and extends the compaction system to handle binary
content references.

**Scope** (already designed in backend docs, needs implementation):

1. Implement `StorageType.S3` in `content_storage_service.py` (currently
   `NotImplementedError`)
2. Add `ContentType.IMAGE`, `ContentType.FILE`, `ContentType.VIDEO`,
   `ContentType.AUDIO` to the memory enum
3. Implement `S3StoredContent` model and `ContentReference` with S3 fields
4. Add S3 configuration (`S3_BUCKET_NAME`, `S3_REGION`, credentials)
5. Implement presigned URL generation for file access
6. Update compaction to handle binary content references (store pointer, not content)

**Frontend impact**: None. The frontend already renders file URLs (from Phase 1/3).
Switching from local URLs to S3 presigned URLs is transparent.

---

## 4. Cross-Cutting Concerns

### 4.1 `MessageEntity` Field Growth

After all phases, `MessageEntity` will have accumulated:
- Core fields (id, roomId, content, senderName, etc.)
- Task fields (taskStatus, taskError, etc.)
- HITL fields (hitlRequestId, hitlPrompt, etc.)
- Retry fields (retryAfterSeconds, relatedUserMessageId, targetGroup)
- Artifact fields (artifacts: ArtifactData[])
- Attachment fields (attachments: AttachmentData[])

This is acceptable for now — the entity is a flat bag of optional fields, and the
`mergeIncoming`/`isNoOpUpdate` pattern handles the enumeration. If the field count
exceeds ~40, consider grouping into sub-objects (`entity.hitl.*`, `entity.task.*`).

### 4.2 `artifacts` vs `attachments` — Why Two Fields?

| | `artifacts` | `attachments` |
|---|---|---|
| **Source** | Agent output (A2A artifacts) | User input (uploaded files) |
| **Arrives via** | `artifact_update` SSE | Optimistic write from chat input |
| **Parts** | `TextPart`, `FilePart`, `DataPart` (A2A `Part` union) | Flat `{ fileUrl, mimeType, fileName }` |
| **Streaming** | May arrive incrementally (append) | Fully uploaded before message sent |
| **Rendered in** | Agent bubble, below message content | User bubble, above message text |
| **Stored in backend** | `Task.artifacts` (nested in `MessageContent.message_task`) | `MessageContent.attachments` (new field) |

Merging these into a single `parts[]` field would require the user bubble to understand
A2A `Part` types and the agent bubble to understand `AttachmentData`, creating coupling.
Keeping them separate preserves single-responsibility.

### 4.3 Forward-Compatibility of Existing Design Docs

| Doc | Multi-modal impact | Action needed |
|-----|-------------------|---------------|
| `ARTIFACT_RENDERING_DESIGN.md` | **IS Phase 1.** Fully aligned. | None |
| `TOKEN_STREAMING_DESIGN.md` | Token streaming is text-only. Agents stream text tokens; non-text content arrives via `artifact_update`. No conflict. | None |
| `HITL_FRONTEND_DESIGN.md` | HITL replies are text-only in Phase 1. Phase 3 adds optional file attachments to HITL forms. The `respondToHitl` API can accept `attachments[]` as an additive field. | Add note in §9 (Out of Scope): "Image/file replies — see MULTIMODAL_SUPPORT_DESIGN.md Phase 3" |
| `SUPERVISOR_TOGGLE_DESIGN.md` | No multi-modal interaction. | None |
| `MESSAGE_PAGINATION_DESIGN.md` | Paginated messages may include attachments/artifacts. The `convertApiMessageToIncoming` changes (Phase 3) handle this. Pagination itself is content-agnostic. | None |
| `TASK_RETRY_DESIGN.md` | Retry re-sends the original text. If the original message had attachments, they should be re-sent too. The `retryMessage` function should forward `userEntity.attachments` to `sendUserMessage`. | **Done**: §4.6 updated — happy path forwards `userEntity.attachments`; fallback and DB-hydrated paths documented as known limitation (attachments not persisted on `RoomMessage`). |
| `DEAD_CODE_CLEANUP.md` | Dead `FilePart`/`DataPart` re-exports are in `src/lib/types/index.ts`, not in `src/lib/api/index.ts` (the cleanup target). No conflict. | None |

### 4.4 Security Considerations

| Risk | Mitigation |
|------|-----------|
| Malicious file upload (XSS via SVG, zip bombs) | Server-side MIME validation, file size limit (10MB), SVG sanitization or rejection |
| Presigned URL leakage | Short TTL (1 hour), room-scoped access check before URL generation |
| Base64 payload size in SSE | Prefer URI-based `FilePart` over `FileWithBytes` for large files. Backend should convert inline bytes to stored URLs before SSE broadcast. |
| Content-type spoofing | Validate actual file content (magic bytes) against declared MIME type |
| Memory exhaustion from large images in store | Store URLs, not bytes. `MessageEntity.attachments` holds file metadata, not content. |

---

## 5. Implementation Order

```
Phase 1 (Frontend)     → ARTIFACT_RENDERING_DESIGN.md
    No backend changes. Can ship immediately.

Phase 2 (Backend)      → Dynamic acceptedOutputModes
    Unlocks multi-modal agent output. ~1 day.
    After this, agents can return images in artifacts.

Phase 3 (Full-Stack)   → User file input
    Depends on: Phase 1 (to render user-sent images).
    ~3-5 days frontend + ~2 days backend.

Phase 4 (Backend)      → S3 storage
    Depends on: Phase 3 (files need persistent storage).
    Already designed in CONTEXT_MEMORY_SYSTEM_DESIGN.md §6.8. ~2-3 days.
```

---

## 6. Key Decisions

| Decision | Rationale |
|---|---|
| 4 phases, not big-bang | Each phase is independently testable and shippable. Phase 1 can be deployed today with mock data. |
| `artifacts` and `attachments` are separate fields | Different sources (agent vs user), different rendering (agent bubble vs user bubble), different storage (Task.artifacts vs MessageContent.attachments). Merging creates coupling. |
| Phase 2 uses `PLATFORM_SUPPORTED_MODES` constant | Single source of truth for what the frontend can render. Easy to expand as capabilities are added. |
| Phase 3 uploads files before sending message | Ensures `file_url` is available for immediate rendering. No "uploading..." placeholder state in the message. Trade-off: slower send for large files. |
| Phase 4 reuses existing §6.8 S3 design | The backend already has detailed S3 storage schemas and integration points. No need to redesign. |
| `PartRenderer` is reusable across phases | Phase 1 uses it for artifacts; Phase 3 can reuse it for user attachment previews if desired (though user bubbles may prefer a simpler inline `<img>`). |

---

## 7. Out of Scope

- Audio/video recording in chat (Phase 5+ — requires media capture APIs)
- Real-time collaborative editing of DataPart content
- File versioning or edit history
- Agent-to-agent file transfer (handled transparently by A2A protocol)
- OCR or image understanding on uploaded files (agent capability, not platform)
- CDN layer for frequently accessed files

---

## 8. Testing Strategy

### Phase 1
- See `ARTIFACT_RENDERING_DESIGN.md` testing section

### Phase 2
- Unit test `_resolve_accepted_modes()` with various agent card configurations
- Integration test: agent with `defaultOutputModes: ["text", "image"]` returns an
  image artifact; verify it reaches the frontend via SSE

### Phase 3
- Unit test `uploadFile()` API client
- Unit test `AttachmentPreview` component with image and file attachments
- Unit test `handlePaste` with image clipboard data
- Unit test `sendUserMessage` with attachments
- Integration test: upload image → send message → verify image renders in user bubble
- Edge case: upload fails mid-flight (network error) — show retry on attachment preview
- Edge case: file exceeds size limit — show error before upload
- Edge case: unsupported MIME type — show validation error

### Phase 4
- Unit test `ContentStorageService` S3 expansion (currently `NotImplementedError`)
- Unit test presigned URL generation and TTL
- Integration test: upload file → compact room memory → verify content reference
  resolves to S3 presigned URL
