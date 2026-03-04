# Agent Groups — Custom Group Targeting System

> **Status: Implemented**

---

## 1. Overview

Agent Groups let users create named sets of agents and target messages to specific groups
instead of broadcasting to all agents in a room. The system provides three targeting modes:

- **All Agents** — broadcast to every active agent on the platform (default for rooms with no agents).
- **Room Team** — send only to agents assigned to the current room (default when room has agents).
- **Custom Groups** — user-created named groups with arbitrary agent membership.

Users manage groups via a modal dialog and select the active target via a dropdown in the
chat input. The selected group persists per room in `localStorage` so it survives page
refreshes.

---

## 2. Architecture

```
                          ┌─────────────────────────┐
                          │   useGroupManagement()   │
                          │  (central state hook)    │
                          └──────────┬──────────────┘
                                     │
                    ┌────────────────┼────────────────┐
                    ▼                ▼                ▼
          ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐
          │GroupSelector │  │GroupManagement│  │  Room Page / Hook │
          │ (dropdown)   │  │Modal (CRUD)  │  │ (sendUserMessage) │
          └─────────────┘  └──────────────┘  └──────────────────┘
```

### Data Flow

1. **On room entry**: `useGroupManagement` loads groups from the API via `listAgentGroups(userId)`.
2. **Default selection**: If no override is set, the effective group is `room_team` (when `roomAgentCount > 0`) or `all_agents`.
3. **User selects a group**: `handleGroupChange(groupId)` sets `overrideGroup` and persists to `localStorage` under key `room-{roomId}-override-group`.
4. **On message send**: `room-chat-input.tsx` reads `selectedGroup` and passes it as `targetGroup` to `onSubmit()`. If `@mentions` are active, `targetGroup` is `undefined` (mentions override group targeting).
5. **Clear override**: User clicks `X` on the dropdown, which calls `handleClearOverride()` to revert to the default and remove the localStorage key.

### CRUD Lifecycle

| Operation | API Function | Endpoint |
|---|---|---|
| Create | `createAgentGroup(...)` | POST `/agentGroups` |
| List | `listAgentGroups(userId)` | GET `/agentGroups?owner_id=...` |
| Read | `getAgentGroup(groupId)` | GET `/agentGroups/{group_id}` |
| Update | `updateAgentGroup(...)` | PUT `/agentGroups/{group_id}` |
| Delete | `deleteAgentGroup(groupId)` | DELETE `/agentGroups/{group_id}` |

After any mutation, `onGroupsChange()` re-fetches the group list. After creation,
`handleGroupCreated(group)` also auto-selects the new group as the active override.

---

## 3. Key Design Decisions

- **`agents` is `string[]` (IDs only)**: Groups store agent IDs, not full agent objects. Resolution to display names happens at render time via `agentNameMap`.
- **`type` discriminator**: Groups have `type: "builtin" | "user"`. Built-in groups (`all_agents`, `room_team`) are constants — never returned by the API or editable. Only `user` groups appear in the management modal.
- **Override/default model**: The selector has a two-tier model — a *default* (derived from room agents) and an *override* (explicit user choice). This avoids forcing users to manually select "Room Team" on every room entry while still allowing manual override.
- **localStorage scoped per room**: Key pattern `room-{roomId}-override-group` ensures different rooms can have different active groups.

---

## 4. Code References

| Concept | File | Notes |
|---|---|---|
| Type definitions & constants | `src/lib/types/agent-group.ts` | `AgentGroup`, `BUILTIN_GROUP_ALL_AGENTS`, `BUILTIN_GROUP_ROOM_TEAM`, `isBuiltinGroup()`, `getGroupDisplayName()` |
| API client (CRUD) | `src/lib/api/agent-group.ts` | 5 functions wrapping REST endpoints via `apiClient` |
| Central state hook | `src/hooks/useGroupManagement.ts` | State, localStorage persistence, CRUD orchestration, agent loading |
| Dropdown selector | `src/components/group-selector.tsx` | Display logic (mentions > default > override), edit/delete inline buttons, tooltips |
| Management modal | `src/components/group-management-modal.tsx` | 4-mode state machine (list/create/edit/delete-confirm), `AgentSelector` integration |
| Chat input integration | `src/components/room-chat-input.tsx` | `selectedGroup` prop, mention override logic (lines 614-617) |

---

## 5. Known Limitations

- **localStorage restoration is consumer-side**: `useGroupManagement` writes to localStorage but does not read on mount. The room page (`src/app/c/room/[id]/page.tsx`) is responsible for reading the persisted override and calling `handleGroupChange()` during initialization. Any new consumer of the hook must replicate this restoration logic.
- **No server-side group validation**: If a group is deleted while another tab has it selected, the selector shows a stale group ID. The dropdown gracefully falls back on next render but there is no proactive staleness check.
- **Agent loading is eager**: The modal loads all active agents when opened, which may be slow for platforms with hundreds of agents.
- **Groups are user-scoped, not room-scoped**: A group created in one room is visible in all rooms. There is no room-level group concept.
