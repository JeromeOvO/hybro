# HYBRO Frontend Redesign - Implementation Plan

## Goal
Redesign hybro.ai to be simple, concise, and intuitive. Align the UI with HYBRO's new positioning as **the interoperability layer for AI agents**.

## New Positioning
> HYBRO is the interoperability layer for AI agents, enabling reliable agent-to-agent and human-agent collaboration across tools, environments, and organizations.

## Key Product: a2a-adapter
> Open Source A2A Protocol Adapter SDK that converts agents built by any agent framework (CrewAI, LangChain, LangGraph, n8n, OpenClaw, etc.) into A2A-compatible and interoperable agents.

---

## Route Structure (7 routes, down from 10)

| Route | Purpose | Status |
|---|---|---|
| `/` | Landing page (unauth) / redirect to `/chat` (auth) | **New** |
| `/chat` | New chat entry | Simplify |
| `/room/[id]` | Active chat room | Fix duplication |
| `/agents` | Agent network with "My Agents" toggle | **Merge** workspace into this |
| `/agents/[id]` | Agent profile | Fix bugs |
| `/agents/register` | Agent registration | Add a2a-adapter helper |
| `/pricing` | Pricing | Keep (wire up later) |

### Routes Removed
- `/workspace` → merged into `/agents` as a toggle
- `/inspector` → external link only (from landing page + registry)
- `/about` → replaced by landing page for unauth users

---

## Sidebar Navigation (simplified)

```
💬 New Chat           /chat
🤖 Agents             /agents
📋 Register Agent     /agents/register
─── History ───
  Room 1              /room/[id]
  Room 2              /room/[id]
─── Footer ───
  Discord · Upgrade · User
```

Removed from sidebar: Inspector, Workspace (merged), About HYBRO.

---

## Implementation Phases

### Phase 1: P0 - Bug Fixes
**Files affected:** `workspace/page.tsx`, `agent/profile/[id]/page.tsx`

1. **Fix `grid-cols-15` typo** in `workspace/page.tsx` line 259
   - Change `grid-cols-15` → `grid-cols-1`

2. **Fix duplicate delete button** in `agent/profile/[id]/page.tsx`
   - Lines 690-708: Bottom delete button has no AlertDialog confirmation
   - Lines 307-339: Top delete button has AlertDialog (correct)
   - Remove the bottom duplicate, keep the top one with AlertDialog

3. **Fix duplicate capabilities array** in `agent/profile/[id]/page.tsx`
   - Lines 410-429: Capabilities array is defined twice
   - Deduplicate into a single array with proper empty-state check

### Phase 2: P0 - Extract `useGroupManagement` Hook
**Files affected:** `chat/page.tsx`, `room/[id]/page.tsx`, new `hooks/useGroupManagement.ts`

Extract duplicated group management logic (~150 lines duplicated across two files):
- State: `groups`, `loadingGroups`, `selectedGroup`, `isOverride`, `groupManagementOpen`, `groupAction`, `availableAgents`, `loadingAgents`
- Functions: `handleGroupsChange`, `handleCreateGroup`, `handleEditGroup`, `handleDeleteGroup`, `handleGroupCreated`, `handleGroupChange`, `handleClearOverride`

### Phase 3: P1 - Landing Page
**Files affected:** `(home)/page.tsx`, `(root)/layout.tsx`

Build auth-aware landing page:
- **Authenticated users**: redirect to `/chat` (current behavior)
- **Unauthenticated users**: show landing page with new positioning

Landing page structure (3 sections only):
1. Hero: positioning statement + two CTAs (Get Started / Try the Chat)
2. How it Works: Adapt → Connect → Collaborate (one row, text only)
3. Footer CTAs: Docs, GitHub, Discord, email

### Phase 4: P1 - Merge Workspace into Agents Page
**Files affected:** `agent/page.tsx`, `workspace/page.tsx`, `nav-items.ts`

- Add "All Agents" / "My Agents" toggle to `/agents` page
- "My Agents" filters client-side where `provider_id === userId`
- Show compact stats bar (agent count, likes) when "My Agents" is selected
- Remove `workspace/page.tsx` route
- Update sidebar nav items

### Phase 5: P1 - Registry Page a2a-adapter Helper
**Files affected:** `agent/registry/page.tsx`

Add one line of helper text below the URL input:
> "New to A2A? Use a2a-adapter to convert any agent. [Learn more ↗]"

Also: extract repeated capability badge Tailwind classes into a small helper.

### Phase 6: P2 - Simplify Sidebar Navigation
**Files affected:** `nav-items.ts`, `app-sidebar.tsx`

- Remove Inspector, About, Workspace from nav items
- Add "Register Agent" nav item
- Keep Discord + Upgrade in footer
- Restructure URLs: `/agent` → `/agents`, `/agent/registry` → `/agents/register`

### Phase 7: P2 - Extract Shared Agent List Hooks
**Files affected:** `agent/page.tsx`, new `hooks/useFilteredAgents.ts`, new `hooks/useDropdown.ts`

Extract `useDropdown`, `useFilteredAgents`, `STATUS_OPTIONS`, and `getStatusLabel` into shared hooks so the merged agents page is clean.

### Phase 8: P2 - Refactor Agent Profile
**Files affected:** `agent/profile/[id]/page.tsx`

Split the 715-line component into subcomponents (same route):
- `AgentProfileView` (identity, capabilities, skills)
- `AgentSettings` (visibility, rate limits, delete - owner only)

---

## Implementation Order

```
Phase 1: Bug fixes (small, immediate quality) ✅ DONE
Phase 2: Extract useGroupManagement hook (code debt) ✅ DONE
Phase 3: Landing page (new positioning, first-visit experience) ✅ DONE
Phase 4: Merge workspace into agents (one fewer route/nav item) ✅ DONE
Phase 5: Registry a2a-adapter helper (developer funnel) ✅ DONE
Phase 6: Simplify sidebar (align nav with new IA) ✅ DONE
Phase 7: Extract shared hooks — CANCELLED (already handled in Phase 4)
Phase 8: Refactor agent profile (fix bugs, deduplicate) ✅ DONE
```

---

## Phase 9: P1 - Developer-First Navigation & Landing Page
**Priority:** High — developers are the primary early-adopter audience.

**Files affected:** `nav-items.ts`, `(home)/page.tsx`, `app-sidebar.tsx`

1. **Add "Developers" to sidebar navigation** — prominent position (second item, right after "New Chat") with `Code2` icon
2. **Update landing page** — add demo video embed with link to full video (`https://youtu.be/ZUQrnlBSsLg`), add "For Developers" CTA linking to `/developers`
3. **Landing page demo section** — show the video between "How it works" and Footer CTAs

### Phase 10: P1 - Enhanced Developer Documentation Page
**Priority:** High — this is the developer portal / docs hub.

**Files affected:** `developers/page.tsx`, new `components/framework-badges.tsx`

Redesign the developers page as a comprehensive developer documentation hub:
1. **Hero** — "Developer Documentation" with demo video link and a2a-adapter branding
2. **Demo Video** — prominent placement with full video link (`https://youtu.be/ZUQrnlBSsLg`)
3. **Getting Started** — expanded quick start: install, create agent, run
4. **Architecture Overview** — how agents connect via A2A protocol through HYBRO
5. **Supported Frameworks** — reusable `FrameworkBadges` component with distinctive styling
6. **Code Examples** — multiple examples: basic agent, agent with skills, streaming
7. **Next Steps funnel** — Test (Inspector) → Register → Chat
8. **Resources** — GitHub, Inspector, Discord, docs, contact

### Phase 11: P2 - Reusable Framework Badges Component
**Files affected:** new `components/framework-badges.tsx`, `(home)/page.tsx`, `developers/page.tsx`

1. Create `FrameworkBadges` component with consistent, attractive framework display
2. Replace plain text framework lists on landing page and developers page
3. Each badge gets a subtle color/icon treatment

### Phase 12: P2 - Replace sessionStorage with Zustand Store
**Files affected:** `hooks/useChatRoomCreation.ts`, `room/[id]/page.tsx`, `stores/room-ui-store.ts`

1. Add `pendingInitialMessage` and `pendingTargetGroup` to the existing Zustand `room-ui-store`
2. Update `useChatRoomCreation` to write to Zustand instead of `sessionStorage`
3. Update `room/[id]/page.tsx` to read from Zustand and clear after use
4. Remove all `sessionStorage` usage for message passing

---

## Implementation Order (Continued)

```
Phase 9:  Developer-first navigation & landing page (developer funnel)   ✅ DONE
Phase 10: Enhanced developer documentation page (developer portal)       ✅ DONE
Phase 11: Reusable framework badges component                           ✅ DONE
Phase 12: Replace sessionStorage with Zustand store (code quality)       ✅ DONE
```

---

## Deferred (P3, do later)

- Revisit pricing tiers for infrastructure positioning
- Full API reference documentation (when a2a-adapter publishes auto-generated docs)
