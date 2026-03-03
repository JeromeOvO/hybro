# Artifact Rendering Design — A2A Artifact Display

> **Status: Not Started** — Design approved, pending implementation.

**Depends on**: None (backend already emits `artifact_update` SSE events)
**Decoupled from**: All other frontend design docs

---

## 1. Problem Statement

A2A agents can produce structured artifacts — code files, data tables, images, documents
— alongside their text responses. The backend already emits `artifact_update` SSE events
(`services/sse_services.py` lines 275-306) carrying full A2A `Artifact` objects with
typed `Part` arrays. However, the frontend's SSE type union does not include
`artifact_update`, so these events are silently dropped. Users never see agent-produced
artifacts.

---

## 2. Current State

### SSE Type Union (`src/lib/types/sse.ts`)

The `SSEMessage.type` union has 8 values. `artifact_update` is absent:

```typescript
type: 'connected' | 'user_message' | 'agent_response' | 'processing_status'
    | 'heartbeat' | 'error' | 'task_submitted' | 'task_update'
```

### A2A SDK Types (`@a2a-js/sdk`)

The frontend already imports A2A types in `src/lib/types/room.ts`:

```typescript
import type {
  Part, TextPart, FilePart, DataPart, FileWithBytes, FileWithUri,
} from '@a2a-js/sdk'
```

These types describe artifact content. The SDK defines `Artifact` as:

```typescript
interface Artifact {
  artifactId: string
  name?: string | null
  description?: string | null
  parts: Part[]
  metadata?: Record<string, unknown> | null
  extensions?: string[] | null
}
```

Where `Part` is `TextPart | FilePart | DataPart`.

### Message Entity (`src/stores/message-store/types.ts`)

`MessageEntity` has no artifact-related fields.

### Backend SSE Emission (`services/sse_services.py`)

```python
async def send_artifact_update(
    self, room_id, message_id, agent_id, artifact,
    append=False, last_chunk=False,
):
    data = {
        "message_id": message_id,
        "agent_id": agent_id,
        "artifact": artifact,       # Full A2A Artifact object
        "append": append,           # True = accumulate into existing artifact
        "last_chunk": last_chunk,   # True = artifact is complete
        "timestamp": utcnow().isoformat(),
    }
    await self.broadcast_to_room(room_id, "artifact_update", data)
```

---

## 3. Proposed Design

### 3.1 Architecture Overview

```
SSE "artifact_update"
        │
        ▼
useRoomWebhook.handleSSEMessage
        │
        ├── append=false: create new artifact entry on message entity
        ├── append=true:  merge parts into existing artifact
        └── last_chunk=true: mark artifact as finalized
        │
        ▼
MessageEntity.artifacts[]
        │
        ▼
message-bubble.tsx (agent messages)
        │
        ▼
ArtifactList (collapsible section below message text)
        │
        ├── TextPart ──────── MarkdownContent / CodeBlock
        ├── FilePart (URI) ── Download link + preview
        ├── FilePart (bytes)─ Inline image / Download link
        └── DataPart ──────── JSON viewer / Structured table
```

### 3.2 Data Flow

1. Backend emits `artifact_update` with an `Artifact` object, `append` flag, and
   `last_chunk` flag.
2. `handleSSEMessage` in `useRoomWebhook.ts` receives the event and upserts the
   artifact data onto the parent message entity (identified by `message_id`).
3. If `append=false`: a new `ArtifactData` entry is created in the message's
   `artifacts` array, keyed by `artifactId`.
4. If `append=true`: the new `parts` are appended to the existing artifact's `parts`
   array (streaming artifact construction).
5. If `last_chunk=true`: the artifact is marked as finalized (no more parts coming).
6. `message-bubble.tsx` checks `entity.artifacts` and renders an `ArtifactList`
   component below the message text if artifacts exist.

---

## 4. Files to Modify

### 4.1 `src/lib/types/sse.ts` — Add artifact event type

Add to `SSEMessage.type` union:

```typescript
type: '...' | 'artifact_update'
```

Add to `SSEMessage.data`:

```typescript
// Artifact fields (for artifact_update)
// Use the `Artifact` type from `@a2a-js/sdk` — do NOT define a custom
// `ArtifactPayload`. The SDK `Artifact` interface has:
//   { artifactId: string, parts: Part[], name?: string,
//     description?: string, metadata?: Record<string, unknown>,
//     extensions?: string[] }
artifact?: Artifact  // from: import type { Artifact } from '@a2a-js/sdk'
append?: boolean
last_chunk?: boolean
```

### 4.2 `src/stores/message-store/types.ts` — Extend MessageEntity

New type and fields:

```typescript
import type { Part } from '@a2a-js/sdk'

export interface ArtifactData {
  artifactId: string
  name?: string | null
  description?: string | null
  parts: Part[]
  metadata?: Record<string, unknown> | null
  isFinalized: boolean
}
```

Add to `MessageEntity`:

```typescript
// ── Artifacts (A2A artifact_update events) ────────────────
artifacts?: ArtifactData[]
```

Add to `IncomingMessage`:

```typescript
artifacts?: ArtifactData[]
```

### 4.3 `src/stores/message-store/upsert.ts` — Merge artifact arrays

The `mergeIncoming` function must handle artifact merging. When an incoming message
has `artifacts`, merge by `artifactId`:

- If an artifact with the same `artifactId` exists, append new parts to it.
- If it does not exist, add it to the array.
- If `isFinalized` is true on the incoming artifact, set it on the stored one.

**Critical**: Both `mergeIncoming` and `isNoOpUpdate` enumerate fields explicitly.

**`mergeIncoming`** — add to both the `!existing` branch and the existing-entity branch:

```typescript
// ── Artifacts ──
artifacts: incoming.artifacts,

// (existing-entity branch — array merge, NOT simple coalesce):
artifacts: incoming.artifacts !== undefined
  ? mergeArtifacts(existing.artifacts, incoming.artifacts)
  : existing.artifacts,
```

Where `mergeArtifacts` implements the upsert-by-`artifactId` + part-append logic
described above. Extract this as a helper function in `upsert.ts`.

**`isNoOpUpdate`** — **must** include `artifacts` in the comparison. The current
`isNoOpUpdate` enumerates fields explicitly; any field not listed is invisible to the
check. An `artifact_update` SSE event typically only changes `artifacts` — all other
fields (`content`, `taskStatus`, `senderName`, etc.) remain the same. Without an
`artifacts` comparison, the no-op check sees no visible change and **discards the
update**.

Add this line to the `isNoOpUpdate` return expression:

```typescript
existing.artifacts === coalesce(incoming.artifacts, existing.artifacts) &&
```

This is a reference comparison (`===`), which is correct here: `mergeArtifacts` always
returns a new array when parts are appended, so changed artifacts will have a different
reference. Structural deep-equal is unnecessary.

This is the only non-trivial change to the message store.

### 4.4 `src/hooks/useRoomWebhook.ts` — Handle artifact_update SSE

Add a new case to `handleSSEMessage`:

```typescript
case 'artifact_update': {
  const { message_id, agent_id, artifact, append, last_chunk } = msg.data
  if (!message_id || !artifact) break

  const existing = store.entities[message_id]
  const existingArtifacts = existing?.artifacts || []

  let updatedArtifacts: ArtifactData[]
  const idx = existingArtifacts.findIndex(a => a.artifactId === artifact.artifactId)

  if (append && idx >= 0) {
    // Append parts to existing artifact
    updatedArtifacts = [...existingArtifacts]
    updatedArtifacts[idx] = {
      ...updatedArtifacts[idx],
      parts: [...updatedArtifacts[idx].parts, ...artifact.parts],
      isFinalized: last_chunk ?? false,
    }
  } else if (idx >= 0) {
    // Replace existing artifact
    updatedArtifacts = [...existingArtifacts]
    updatedArtifacts[idx] = {
      artifactId: artifact.artifactId,
      name: artifact.name,
      description: artifact.description,
      parts: artifact.parts,
      metadata: artifact.metadata,
      isFinalized: last_chunk ?? false,
    }
  } else {
    // New artifact
    updatedArtifacts = [...existingArtifacts, {
      artifactId: artifact.artifactId,
      name: artifact.name,
      description: artifact.description,
      parts: artifact.parts,
      metadata: artifact.metadata,
      isFinalized: last_chunk ?? false,
    }]
  }

  store.upsertMessage({
    id: message_id,
    roomId,
    messageType: 'agent',
    content: existing?.content || '',
    senderName: existing?.senderName || 'Agent',
    timestamp: msg.timestamp,
    agentId: agent_id,
    artifacts: updatedArtifacts,
  }, 'sse')
  break
}
```

### 4.5 New component: `src/components/artifact-renderer.tsx`

Responsible for rendering a single `ArtifactData` object. Dispatches on `Part` type:

```typescript
interface ArtifactRendererProps {
  artifact: ArtifactData
}

export function ArtifactRenderer({ artifact }: ArtifactRendererProps) {
  return (
    <div className="border rounded-lg overflow-hidden">
      {artifact.name && (
        <div className="px-3 py-2 bg-muted/50 border-b text-sm font-medium">
          {artifact.name}
          {artifact.description && (
            <span className="text-muted-foreground ml-2">{artifact.description}</span>
          )}
        </div>
      )}
      <div className="p-3 space-y-2">
        {artifact.parts.map((part, i) => (
          <PartRenderer key={i} part={part} />
        ))}
      </div>
      {!artifact.isFinalized && (
        <div className="px-3 py-1.5 border-t text-xs text-muted-foreground">
          Loading more content...
        </div>
      )}
    </div>
  )
}
```

### 4.6 New component: `src/components/part-renderer.tsx`

Renders individual A2A `Part` objects. **Bundle note**: `PartRenderer` handles base64
decoding, image rendering, and JSON formatting which can be heavy. Use `next/dynamic`
to lazy-load it so it does not add to the initial chat bundle (Vercel rule
`bundle-dynamic-imports`):

```typescript
import dynamic from 'next/dynamic'
const PartRenderer = dynamic(() => import('./part-renderer'), { ssr: false })
```

| Part Type | Detection | Rendering |
|---|---|---|
| `TextPart` | `part.kind === 'text'` | Render with `MarkdownContent` component (reuse existing). Detect code blocks via metadata. |
| `FilePart` with URI | `part.kind === 'file'` and `part.file.uri` exists | Render as a download link with file name and size. If the URI points to an image (`*.png`, `*.jpg`, `*.gif`, `*.webp`, `*.svg`), show an inline `<img>` preview. |
| `FilePart` with bytes | `part.kind === 'file'` and `part.file.bytes` exists | Decode base64. For images, render inline `<img src="data:...">`. For other file types, render a download button that creates a Blob URL. |
| `DataPart` | `part.kind === 'data'` | Render as a collapsible JSON viewer using `<pre>` with syntax highlighting. If the data is an array of objects, optionally render as an HTML table. |

**Important**: The A2A SDK (`@a2a-js/sdk`) uses `kind` as the part discriminator field
(values: `'text'`, `'file'`, `'data'`), not `type`. The `TextPart`, `FilePart`, and
`DataPart` interfaces all define `kind: "text" | "file" | "data"`. Using `part.type`
will fail TypeScript strict checks and produce runtime mismatches.

### 4.7 `src/components/message-bubble.tsx` — Add artifact section

In the agent message bubble, after the message content, conditionally render:

```tsx
{entity.artifacts && entity.artifacts.length > 0 && (
  <ArtifactList artifacts={entity.artifacts} />
)}
```

### 4.8 New component: `src/components/artifact-list.tsx`

Renders the list of artifacts below a message. If there are multiple artifacts, wrap
each in a collapsible section:

```typescript
interface ArtifactListProps {
  artifacts: ArtifactData[]
}

export function ArtifactList({ artifacts }: ArtifactListProps) {
  if (artifacts.length === 1) {
    return <ArtifactRenderer artifact={artifacts[0]} />
  }
  return (
    <div className="space-y-2 mt-3">
      {artifacts.map(artifact => (
        <Collapsible key={artifact.artifactId} defaultOpen>
          <CollapsibleTrigger className="text-sm font-medium">
            {artifact.name || `Artifact ${artifact.artifactId.slice(0, 8)}`}
          </CollapsibleTrigger>
          <CollapsibleContent>
            <ArtifactRenderer artifact={artifact} />
          </CollapsibleContent>
        </Collapsible>
      ))}
    </div>
  )
}
```

---

## 5. State Management Changes

### 5.1 Message Store

The `artifacts` field is added to `MessageEntity`. The `upsert.ts` merge logic gains
artifact-aware merging (append by `artifactId`). No new store is needed.

### 5.2 Room UI Store

No changes.

### 5.3 React Query

No changes.

---

## 6. Key Decisions

| Decision | Rationale |
|---|---|
| Artifacts stored on `MessageEntity` (not separate store) | 1:1 relationship with messages. Co-location keeps rendering simple and avoids cross-store synchronization. |
| `ArtifactData` wraps SDK `Part[]` with metadata | Adds `isFinalized` for streaming state and a stable `artifactId` key for efficient updates. |
| Streaming via `append` flag | Backend sends artifact parts incrementally. Accumulating into the entity's `parts` array lets the UI progressively render. Artifact streaming is much lower frequency than token streaming (typically 1-5 events per agent response, not 100+), so upsert cost per event is acceptable. If this becomes a bottleneck, the same `requestAnimationFrame` batching from `TOKEN_STREAMING_DESIGN.md` can be applied. |
| Reuse `MarkdownContent` for text parts | Avoids duplicating markdown rendering logic. Existing component handles code highlighting, GFM tables, etc. |
| Inline image rendering for file parts | Images are the most common artifact type. Inline display avoids extra clicks. Non-image files get download links. |
| Collapsible multi-artifact layout | Prevents artifact-heavy messages from overwhelming the chat view. Single artifact renders open by default. |

---

## 7. Error Handling

| Scenario | Behavior |
|---|---|
| `artifact_update` for unknown `message_id` | Create a minimal placeholder message entity with the artifact. When the `agent_response` event arrives later, the entity will be updated with full content via upsert merge. |
| Malformed `artifact` payload (missing `parts`) | Skip the artifact. Log a warning to console. |
| Base64 decode failure on `FilePart.bytes` | Show an error placeholder within the artifact renderer: "Failed to decode file content". |
| Very large artifact (> 1MB text) | Render with virtual scrolling or truncation with "Show full content" expansion. Exact threshold TBD during implementation. |
| `append=true` but no existing artifact | Treat as a new artifact (same as `append=false`). |

---

## 8. Out of Scope

- Artifact editing or modification by the user.
- Artifact persistence to a separate frontend cache or IndexedDB.
- Artifact download-all / zip functionality.
- Artifact diffing between versions (if an agent sends multiple revisions).
- Backend changes — the SSE emission code is already complete.

---

## 9. Testing Strategy

- Unit test `PartRenderer` for each part type: text, file-with-uri, file-with-bytes,
  data.
- Unit test `ArtifactRenderer` with single and multiple parts, finalized and streaming
  states.
- Unit test `ArtifactList` with 1 artifact (no collapsible) and 3+ artifacts
  (collapsible).
- Unit test message store `upsert` with artifact merging: new artifact, append parts,
  replace artifact.
- Unit test `handleSSEMessage` for `artifact_update`: new artifact, append, last_chunk.
- Integration test: mock SSE stream, verify artifacts appear below message bubble.
- Edge case: artifact arriving before `agent_response` (message entity may not exist
  yet).
