# Debate Mode — Agent Discussion Toggle

> **Status: Implemented**

---

## 1. Overview

Debate Mode is a room-level toggle that enables enhanced multi-agent discussions. When
active, the backend orchestrates agents to engage in collaborative problem-solving —
agents can respond to each other's outputs, challenge conclusions, and build on prior
reasoning.

The frontend's role is limited to the toggle UI and persistence. All behavioral differences
are implemented in the backend orchestration layer.

---

## 2. User Flow

1. User opens room settings (via the settings button in the room header or during room creation).
2. The "Debate Mode" switch is displayed below the "Supervisor Mode" switch.
3. User toggles the switch on/off.
4. On save, the setting is persisted to the backend via `updateRoomExtendInfo()`.
5. The backend reads `extend_info.debateMode` to adjust orchestration behavior for subsequent messages.

---

## 3. Architecture

### Form Integration

The toggle is a `<Switch>` component inside a card-styled `FormItem` in `room-setting-form.tsx`:
- Icon: `<MessageCircleMore>` from Lucide.
- Label: "Debate Mode".
- Description: "Enable debate mode for enhanced agent discussions and collaborative problem-solving".

Both `formSchemaRequired` (room creation) and `formSchemaOptional` (room editing) include
`debateMode: z.boolean()`.

### Persistence

The `handleSubmit` function passes `RoomModeOptions { debateMode, useSupervisor }` to the
parent component's `onSubmit` callback.

In `useRoomWebhook.updateRoomSettings()`:
1. `getDebateMode()` reads the current value from `room.extend_info.debateMode`.
2. If the new value differs, `updateRoomExtendInfo(roomId, { ...existingExtendInfo, debateMode })` is called.
3. The room query is refetched to confirm the update.

### Relationship to Supervisor Mode

Debate Mode and Supervisor Mode are independent toggles that can be combined:
- **Neither**: Basic V1 Direct Dispatch.
- **Supervisor only**: Adaptive supervisor loop without debate.
- **Debate only**: Debate-style orchestration without adaptive replanning.
- **Both**: Supervisor-coordinated debate with adaptive replanning.

---

## 4. Code References

| Concept | File | Notes |
|---|---|---|
| Switch UI | `src/components/room-setting-form.tsx` (lines 229-252) | `<MessageCircleMore>` icon + Switch |
| Zod schema | `src/components/room-setting-form.tsx` (lines 26-43) | `debateMode: z.boolean()` |
| `RoomModeOptions` type | `src/components/room-setting-form.tsx` (lines 52-55) | `{ debateMode: boolean; useSupervisor: boolean }` |
| Read current value | `src/hooks/useRoomWebhook.ts` (lines 222-226) | `getDebateMode()` reads `extend_info.debateMode` |
| Persist on save | `src/hooks/useRoomWebhook.ts` (lines 1001-1073) | `updateRoomSettings()` calls `updateRoomExtendInfo()` |

---

## 5. Known Limitations

- **No visual indicator in room header**: Unlike the potential "Supervisor" badge, there is no indicator showing whether Debate Mode is active without opening room settings.
- **Backend behavior is opaque**: The frontend has no visibility into how Debate Mode changes orchestration. There is no differentiation in the UI between debate-mode and non-debate-mode agent responses.
- **No per-message override**: Debate Mode is a room-level setting. Users cannot enable/disable it for a single message.
