# Supervisor Toggle Design — Supervisor V2 Room Setting

> **Status: Implemented** | Core toggle shipped. Optional header badge and processing indicator deferred.

**Depends on**: None (backend already reads `room.extend_info.use_supervisor`)
**Decoupled from**: All other frontend design docs

---

## 1. Problem Statement

The backend supports two orchestration modes for room message processing:

- **V1 Direct Dispatch**: LLM parses the user message, decomposes into per-agent tasks,
  and dispatches them sequentially or in parallel. No adaptive replanning.
- **Supervisor V2**: An adaptive loop where a supervisor LLM iteratively decides the
  next action (delegate, synthesize, clarify, done). Supports parallel dispatch,
  mid-loop clarification, and dynamic replanning.

The orchestration mode is controlled by `room.extend_info.use_supervisor` (boolean).
The backend reads this field in `services/room_services.py` (line ~1450) and
`SupervisorExecutor.py`.

The frontend's room settings form (`src/components/room-setting-form.tsx`) only exposes
`roomName` and `debateMode`. There is no toggle for `use_supervisor`. Users cannot opt
into Supervisor V2 mode.

---

## 2. Current State

### Room Settings Form (`src/components/room-setting-form.tsx`)

Zod schemas define two fields:

```typescript
const formSchemaRequired = z.object({
  roomName: z.string().min(2).max(50),
  debateMode: z.boolean(),
})
```

The submit handler passes `(roomName, selectedAgents, debateMode)`:

```typescript
function handleSubmit(values) {
  onSubmit(roomName, selectedAgents, debateMode)
}
```

The `onSubmit` prop signature:

```typescript
onSubmit: (roomName: string, selectedAgents: {...}, debateMode: boolean) => void
```

### Room Extend Info Update (`src/hooks/useRoomWebhook.ts`)

The `updateRoomSettings` function calls `updateRoomExtendInfo` with only `debateMode`:

```typescript
await updateRoomExtendInfo(roomId, { debateMode }, getToken)
```

### Chat Page Room Creation

The chat page (`src/app/c/chat/page.tsx`) creates rooms via
`RoomSettingForm` and passes the form values to the room creation flow. It does not
set `use_supervisor` in `extend_info`.

### Backend Reading

```python
# services/room_services.py ~line 1450
is_supervisor = (
    room.extend_info.get("use_supervisor", False) if room.extend_info else False
)
```

Default is `False` — rooms use V1 unless explicitly enabled.

---

## 3. Proposed Design

### 3.1 Overview

Add a "Supervisor Mode" switch toggle to the room settings form, alongside the existing
"Debate Mode" toggle. Update the form schema, submit handler, and all callers to include
`useSupervisor` in the data flow. Optionally display a visual indicator in the room
header when Supervisor mode is active.

### 3.2 UI Layout

```
┌─────────────────────────────────────┐
│  Room Settings                      │
│                                     │
│  Room Name: [_______________]       │
│                                     │
│  ─────────────────────────────────  │
│                                     │
│  🎯 Supervisor Mode          [ON]  │
│  Enable AI supervisor to            │
│  coordinate agents with an          │
│  adaptive planning loop             │
│                                     │
│  ─────────────────────────────────  │
│                                     │
│  💬 Debate Mode              [OFF]  │
│  Enable debate mode for enhanced    │
│  agent discussions                  │
│                                     │
│  ─────────────────────────────────  │
│                                     │
│  Agent Selection: [...]             │
│                                     │
│  [Create Room]                      │
└─────────────────────────────────────┘
```

Supervisor Mode is placed before Debate Mode because it is the higher-impact setting.
Both toggles are independent — enabling both means the supervisor uses debate-style
dispatch (all agents in parallel, no synthesis).

---

## 4. Files to Modify

### 4.1 `src/components/room-setting-form.tsx` — Add Supervisor toggle

**Zod schemas**: Add `useSupervisor`:

```typescript
const formSchemaRequired = z.object({
  roomName: z.string().min(2).max(50),
  useSupervisor: z.boolean(),
  debateMode: z.boolean(),
})

const formSchemaOptional = z.object({
  roomName: z.string().max(50).optional().or(z.literal('')),
  useSupervisor: z.boolean(),
  debateMode: z.boolean(),
})
```

**Default values**: Add `useSupervisor: false` to the `useForm` config.

**Form initialization**: Read from `initialData`:

```typescript
form.setValue('useSupervisor', initialData.useSupervisor || false)
```

**Submit handler**: Include `useSupervisor` via the `RoomModeOptions` object:

```typescript
function handleSubmit(values) {
  const options: RoomModeOptions = {
    debateMode: values.debateMode ?? false,
    useSupervisor: values.useSupervisor ?? false,
  }
  onSubmit(roomName, selectedAgents, options)
}
```

**Note**: The actual implementation uses an options object (`RoomModeOptions`) rather
than positional args, following the DRY recommendation below.

**Props interface**: Update `onSubmit` and `RoomFormData`:

```typescript
interface RoomFormData {
  roomName: string
  selectedAgents: { [agentId: string]: string }
  debateMode?: boolean
  useSupervisor?: boolean
}

interface RoomSettingFormProps {
  onSubmit: (
    roomName: string,
    selectedAgents: { [agentId: string]: Agent },
    options: RoomModeOptions,
  ) => void
  // ... rest unchanged
}
```

**Form UI**: Add a new `FormField` for Supervisor Mode between the Room Name field
and the Debate Mode field. Use the same `Switch` pattern as Debate Mode:

```tsx
<FormField
  control={form.control}
  name="useSupervisor"
  render={({ field }) => (
    <FormItem className="flex flex-row items-center justify-between rounded-lg border p-4 bg-card">
      <div className="space-y-0.5 flex-1">
        <FormLabel className="text-base flex items-center gap-2">
          <Target className="h-4 w-4" />
          Supervisor Mode
        </FormLabel>
        <FormDescription className="text-sm">
          Enable AI supervisor to coordinate agents with an adaptive planning loop
        </FormDescription>
      </div>
      <FormControl>
        <Switch
          checked={field.value}
          onCheckedChange={field.onChange}
        />
      </FormControl>
    </FormItem>
  )}
/>
```

Import `Target` from `lucide-react` (distinct from `MessageCircleMore` used by
Debate Mode).

### 4.2 `src/hooks/useRoomWebhook.ts` — Update `updateRoomSettings`

The `updateRoomSettings` callback receives `debateMode` and `useSupervisor` from the
form and calls `updateRoomExtendInfo`:

```typescript
// Before (only debateMode):
await updateRoomExtendInfo(roomId, { debateMode }, getToken)

// After (include use_supervisor, preserving existing extend_info keys):
const updatedExtendInfo = {
  ...(room.extend_info as object || {}),
  debateMode,
  use_supervisor: useSupervisor,
}
await updateRoomExtendInfo(roomId, updatedExtendInfo, getToken)
```

**Important**: The `updateRoomSettings` code (lines 1001-1073) already performs a
shallow merge via `{ ...(room.extend_info as object || {}), debateMode }` to preserve
keys like `initialMessage`. The snippet above follows the same pattern — do NOT replace
the spread with a bare `{ debateMode, use_supervisor }` object, which would drop
existing `extend_info` keys.

Note the field name is `use_supervisor` (snake_case) to match the backend's
`extend_info` key, while the form uses `useSupervisor` (camelCase) for JavaScript
convention.

Also update the `updateRoomSettings` function signature to accept the options object:

```typescript
const updateRoomSettings = async (
  roomName: string,
  selectedAgents: { [agentId: string]: Agent },
  options: RoomModeOptions,
) => { ... }
```

### 4.3 `src/app/c/chat/page.tsx` — Room creation

Update the room creation handler to pass `useSupervisor` through to the room
creation flow. The chat page constructs initial `extend_info` when creating a room:

```typescript
// Include use_supervisor in the initial extend_info
const extendInfo = {
  debateMode,
  use_supervisor: useSupervisor,
}
```

### 4.4 `src/hooks/useRoomWebhook.ts` — Read `useSupervisor` from room settings

When constructing `RoomFormData` for the settings dialog (from the room query data),
include `useSupervisor`:

```typescript
const formData: RoomFormData = {
  roomName: room.room_name,
  selectedAgents: room.room_agent_set || {},
  debateMode: (room.extend_info as any)?.debateMode || false,
  useSupervisor: (room.extend_info as any)?.use_supervisor || false,
}
```

---

## 5. Optional Enhancements

### 5.1 Room Header Badge

Show a visual indicator in the room header when Supervisor mode is active:

```tsx
{roomSettings?.extend_info?.use_supervisor && (
  <span className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-violet-100 dark:bg-violet-500/15 text-violet-700 dark:text-violet-400">
    Supervisor
  </span>
)}
```

This gives users a persistent reminder of which orchestration mode is active.

### 5.2 Processing Indicator

During message processing, if Supervisor mode is active, the processing placeholder
could show "Supervisor coordinating agents..." instead of "Processing your request..."
to provide clearer feedback about what is happening.

---

## 6. State Management Changes

### No new stores or state needed.

- Room settings are fetched via React Query (`['room', roomId]`).
- `extend_info` is part of the room object returned by `inquiryRoomSetting`.
- The form reads `useSupervisor` from `extend_info.use_supervisor` during
  initialization.
- The form writes `use_supervisor` back to `extend_info` on submit via
  `updateRoomExtendInfo`.

---

## 7. Key Decisions

| Decision | Rationale |
|---|---|
| Supervisor toggle is independent of Debate Mode | Both can be on simultaneously. Debate mode affects dispatch strategy within the supervisor loop (all-agent parallel dispatch, skip synthesis). Making them independent avoids confusing interactions. |
| Field name `use_supervisor` in extend_info (snake_case) | Matches the backend convention. The backend reads `extend_info.use_supervisor`. |
| Placed above Debate Mode in the form | Higher-impact setting gets more visual priority. |
| Default off | Matches backend default (`False`). V1 dispatch is simpler and sufficient for single-agent rooms. |
| `Target` icon from lucide-react | Visually distinct from Debate Mode's `MessageCircleMore`. Conveys "coordination/orchestration". |

---

## 8. Error Handling

| Scenario | Behavior |
|---|---|
| `updateRoomExtendInfo` fails | Show error toast via existing error handling. Room settings dialog remains open. The toggle reverts to the previous value (form is not reset on error). |
| Backend does not support Supervisor V2 (older version) | The `use_supervisor` field is ignored by older backends. No error. V1 dispatch is used. |
| Room has no `extend_info` | Default to `use_supervisor: false`. The form initializes the toggle as off. |

---

## 9. Out of Scope

- Supervisor V2 backend implementation changes (already complete).
- Supervisor trajectory visualization (showing the supervisor's plan steps in the UI).
- Per-message supervisor override (enabling/disabling supervisor for a single message).
- Auto-enabling supervisor for complex queries (could be a future "auto" mode).
- Backend validation of `extend_info` fields (currently schemaless `dict[str, Any]`).

---

## 10. Testing Strategy

- Unit test: `RoomSettingForm` renders Supervisor Mode toggle.
- Unit test: form submit includes `useSupervisor` in the callback.
- Unit test: form initialization reads `useSupervisor` from `initialData`.
- Unit test: `updateRoomSettings` calls `updateRoomExtendInfo` with
  `{ debateMode, use_supervisor }`.
- Integration test: toggle Supervisor Mode on, save room settings, verify
  `extend_info` contains `use_supervisor: true`.
- Edge case: room with no prior `extend_info` — verify `useSupervisor` defaults to
  `false`.

---

## 11. Code References

| Concept | File | Notes |
|---|---|---|
| Switch UI | `src/components/room-setting-form.tsx` (lines 201-224) | `<Target>` icon + Switch toggle in card-styled FormItem |
| Zod schema | `src/components/room-setting-form.tsx` (lines 26-43) | `useSupervisor: z.boolean()` in both required and optional schemas |
| Submit handler | `src/components/room-setting-form.tsx` (lines 155-161) | Passes `RoomModeOptions { debateMode, useSupervisor }` to parent |
| Backend persistence | `src/hooks/useRoomWebhook.ts` (lines 1001-1073) | `updateRoomSettings()` writes `use_supervisor` to `extend_info` |
| Read-back | `src/hooks/useRoomWebhook.ts` (lines 229-233) | `getSupervisorMode()` reads `room.extend_info.use_supervisor` |

### Deferred Items

- Room header badge showing "Supervisor" when active (Section 5.1 of design).
- Processing indicator change ("Supervisor coordinating agents...") (Section 5.2 of design).
