# Hybro UI/UX Redesign Spec

Status: Implemented (2026-10-10)
Scope: frontend only. Backend contracts, room chat internals, and streaming are unchanged.

## 1. Goal

Make Hybro read as a network you operate: a network of A2A agents, organized into subnets, that outside agents and users reach through MCP or the HTTP API to discover and call agents. Room chat remains the place where a person tries several agents in one interface.

Success means a user can, without reading docs:

1. Tell at a glance whether the instance is healthy and what needs attention.
2. See every agent, which subnets it belongs to, and its status, in either a graph or a structured view.
3. Understand that MCP and API are the network's front door for external callers, and copy a connection snippet scoped to the whole network or one subnet.

## 2. Decisions Agreed

| Topic | Decision |
|---|---|
| Change size | Restructure navigation and pages, refresh visuals. Keep chat internals and backend contracts. |
| Home | New Overview page at `/`. |
| Network management | One Network workspace with a Topology / Structure view switch. Replaces the separate Agents and Subnets pages. |
| Subnet meaning | A subnet is a named group of agents used as a request scope (`group_id`). |
| Graph | 2D disjoint force-directed SVG graph (d3 `forceX`/`forceY`), subnet and agent nodes only, motion from physics only. |
| Visual direction | Neutral, low-color UI. Hybro teal is the single accent; purple appears only in the logo. See section 7. |
| Rooms | Separate area: restyle, plus the subnet terminology and shared subnet dialog in the room group selector (section 6.5), and room status (section 6.4). No chat behavior change. |
| Routes | New routes; old URLs redirect. |
| Terminology | A saved agent group is a "Subnet" everywhere, including rooms; one shared create and edit dialog. |
| Network actions | Keep all three actions as two buttons: Create subnet is the primary button; Register by URL and Discover local agents live in one "Add agent" menu. |
| Room status | Show Queued, Running, and Needs input from room history `status`. |
| Navigation sidebar | Keep the existing collapse / expand toggle; when collapsed, every item shows as an icon. |
| Overview header | No page header on Overview; it opens on the metric row. |
| Structure view | Defaults to a list of every agent, with a List / By subnet layout switch (section 6.2.2). |
| Agent detail | Restyle the current agent detail page and add a Subnets section (section 6.2.3). |
| Buttons | One button system and one clickable-row pattern across the product (section 7.1). |

## 3. Users

Developers and operators running their own Hybro instance, locally or on their own cloud host. Each instance belongs to its user, so the new pages have no signed-out or visitor variants. Per-account permission control is a future enterprise concern and out of scope.

## 4. Design Principles

Every screen is reviewed against these before it ships.

| Principle | Rule in Hybro |
|---|---|
| Visibility of system status | One shared status indicator used everywhere, with one label and token per state (section 7.2). Gateway status is visible on Overview and Connect. Data panels show a "last updated" time. |
| Consistency | One page header pattern (title and one-line description; actions sit in the page toolbar), used on every page except Overview. One button system and one clickable-row pattern (section 7.1). One details panel for graph and table selections. One set of empty, loading, and error components. |
| Recognition over recall | Subnet chips on agents, member counts on subnets, copyable scope IDs wherever a subnet appears. |
| Clear hierarchy | At most one primary button per view. Teal marks only the primary action, the current selection, and subnets; status colors are reserved for status. Everything else is neutral gray. |
| Error prevention and recovery | Destructive actions confirm. Errors state what happened and offer Retry. Empty states name the next step. |
| Honest copy | UI never claims more than the backend guarantees (see section 8.3 on scope). |
| Accessibility | WCAG 2.1 AA contrast in both themes, full keyboard operation, visible focus, 44px touch targets on mobile, `prefers-reduced-motion` respected. |

## 5. Information Architecture

### 5.1 Navigation

```text
Overview   /
Network    /network            (?view=topology | structure)
Rooms      /chat, /room/[id]
Connect    /connect/mcp, /connect/api
---
Docs, Discord, user menu
```

The chat-history secondary sidebar stays on Rooms pages only.

Collapse and expand: the existing toggle stays (panel icon next to the logo; when collapsed, the logo is the expand button and shows the panel icon on hover). Expanded, the sidebar is 232px wide. Collapsed, it is a 64px icon rail where every item (Overview, Network, Rooms, Connect, Docs, Discord, user menu) shows its icon with a tooltip and an accessible name; the active item keeps its highlight. The Rooms needs-input count becomes a dot on the Rooms icon. Collapse state persists as it does today. On mobile the sidebar stays an off-canvas sheet.

### 5.2 Route changes

| New route | Replaces | Notes |
|---|---|---|
| `/` | redirect to `/core` | Renders Overview. `/core` and other marketing pages remain reachable at their URLs. |
| `/network` | `/agents`, `/networks` | Workspace with view switch. |
| `/network/agents/[id]` | `/agents/[id]`, `/manage/agents/[id]` | Agent detail page, restyled (section 6.2.3). |
| `/network/agents/new` | `/agents/new`, `/manage/agents/new` | Register agent, restyled. |
| `/connect/mcp`, `/connect/api` | `/access/mcp`, `/access/api` | Connect pages. |

Old routes become server redirects to the new ones. `routes.ts`, `consumer-nav.ts`, `robots.ts`, sidebar and header marketing-page lists, markdown agent links, and affected tests are updated together.

New pages (Overview, Network, Connect) do not branch on sign-in state; the existing auth adapter is unchanged. The "Sign in to manage subnets" gate on the current Networks page is removed. The marketing header "Explore" link points to `/network`.

## 6. Screens

### 6.1 Overview

Purpose: "Is my network healthy, and what needs my attention?"

Overview has no visible page header (no title, description, or buttons); it opens directly on the metric row. It still renders a visually hidden `<h1>Overview</h1>` for screen readers and heading navigation. Content, top to bottom:

1. Metric row (4 tiles): Agents (online / offline split), Subnets (plus unassigned agent count), Gateway (MCP status from `/hybro-mcp`, API reachability), Rooms (total, plus counts for Running and Needs input from room history `status`).
2. Needs attention list: rooms that need input (link to the room), offline or unreachable agents, agents in no subnet, MCP unavailable. Each row is a clickable row (section 7.1) that links to the fix location. Empty state: "Everything is running".
3. Mini topology preview (static, non-interactive render of the graph) linking to `/network`.
4. Recent rooms (from room history) and most-called agents with success rate (`call_success_count / call_count`). Labeled "Room calls": direct MCP and API sends do not update these counters.

Data sources (existing APIs only): `getAllAgents`, `getAgentsByProviderId`, `listAgentGroups`, `listRoomHistory`, `/hybro-mcp`.

Backend facts behind this (2026-10-10): the MCP server (`MCP/server.py`) is a stateless wrapper exposing `discover_agents` and `send_agent_message`, forwarding to `GET /agents/discovery` and `POST /agents/messages`. Direct sends (`execution/direct_agent.py`) are non-persistent and do not increment call counters; only room calls through `a2a_adapter/runtime_service.py` do. `MetricsCollector` is a no-op. No gateway traffic is recorded anywhere.

### 6.2 Network workspace

Page header: title "Network" and a one-line description only.

Shared toolbar, one row below the header: view switch (Topology / Structure), search (agent name, skill, tag, subnet), then right-aligned actions. All three actions from today are kept, as two buttons:

- Add agent: secondary button that opens a menu:
  - Register by URL: opens `/network/agents/new` to register a remote A2A agent by its card URL.
  - Discover local agents: runs the existing local discovery (`discoverLocalAgents`) and reports how many agents were found.
- Create subnet: the primary button. Opens the shared subnet dialog (section 6.5).

On narrow screens the actions wrap below the search.

#### 6.2.0 URL state

View, search, scope, layout, and selection are independent states stored in the URL:

| Param | Values | Meaning |
|---|---|---|
| `view` | `topology` (default), `structure` | Active view. |
| `q` | text | Search text. |
| `scope` | `all` (default), `unassigned`, `subnet:<id>` | Structure view scope (scope list selection). |
| `layout` | `list` (default), `subnet` | Structure view layout: one flat list, or grouped by subnet. |
| `select` | `agent:<id>`, `subnet:<id>` | Item shown in the details panel. |

They do not change each other. Example: a user filters the table to subnet A (`scope=subnet:A`), searches "writer" (`q=writer`), and opens agent B's details (`select=agent:B`); the scope stays subnet A even if B also belongs to another subnet or none.

- Search in Topology highlights matching nodes and their membership edges without hiding other nodes. Non-matching nodes dim. The search highlight and the hover highlight use the same visual treatment; hover takes precedence while active.
- Search in Structure filters the agent list within the selected scope. Scope list counts show matches when a search is active.
- Switching views preserves the other params. Refreshing or sharing the URL restores them.
- Default values are omitted from the URL. Unknown or stale IDs (deleted subnet or agent) fall back to the default with no error page.
- Updating `q` replaces the history entry (no entry per keystroke); changing `view`, `scope`, `layout`, or `select` pushes one.

Shared details panel (right side, sheet on mobile):

- Subnet: name, description, scope ID (copy), member list with status, actions: Manage members, Connect with this scope (opens Connect with the scope preselected), Delete.
- Agent: avatar, name, version and provider, description, status, source (Local / Remote / Hub), room call count, skills, subnets as chips, actions: Open details (agent detail page, section 6.2.3), Add to subnet (menu, below).

Add to subnet (details panel and agent detail page) is a secondary menu button, not the subnet dialog:

- The menu lists every subnet with a check mark on those the agent already belongs to. Choosing a subnet adds or removes the agent immediately (membership update only; other subnet fields are not sent), with a banner confirming the change and Retry on failure.
- The last item, "Create subnet…", opens the shared subnet dialog (section 6.5) with this agent preselected.
- Empty state (no subnets yet): the menu shows only "Create subnet…".

#### 6.2.1 Topology view

Rendering: SVG, d3-force, evolved from the existing `network-canvas-controller.ts`.

- Nodes: two types only, subnet and agent. All nodes are the same radius; type is shown by color.
- Edges: one per membership. An agent in several subnets has several edges and settles between them.
- Forces: link, many-body charge, collide, `forceX` and `forceY` (disjoint layout keeps unconnected agents and separate clusters in view).
- Motion: physics only. The simulation settles and stops. Dragging a node reheats it; release lets it settle. No ambient drift, pulse, or particles. With `prefers-reduced-motion`, layout is computed before first paint and dragging moves only the dragged node.
- Status: offline and unverified agents are hollow with a dashed muted outline.
- Hover / focus: the node, its neighbors, and connecting edges stay at full opacity; everything else dims. Connecting edges use the brand teal.
- Click / Enter: sets `select`, keeps the highlight, opens the details panel. Escape or empty-canvas click clears `select`. Topology does not read or change `scope`.
- Labels:
  - Subnet labels: always visible by default.
  - Agent labels: shown when the agent is hovered or focused, or when a subnet it belongs to is hovered or focused, or when it is selected or related to the selection.
  - Display menu in the canvas controls with two switches, persisted in `localStorage`: "Always show subnet labels" (default on; off falls back to hover/focus) and "Always show agent labels" (default off).
- Controls (existing, restyled): zoom in, zoom out, zoom readout, fit all, reset layout, plus the new Display menu.
- Keyboard: keep current model (Tab through nodes, Enter for details, arrow keys pan, plus/minus zoom, 0 fits, Escape closes).
- Colors come from theme tokens (section 7); the hardcoded `#1b1b1f`-style values in `networks.css` are removed so the graph follows light and dark themes.

#### 6.2.2 Structure view

Purpose: answer "which agents do I have?" first, and "how are they grouped?" second.

- Left: scope list. "All agents" (default) and "Unassigned", each with a count, then a "Subnets" group listing each subnet with its member count and an info button. Clicking an item sets `scope`; the info button sets `select=subnet:<id>`. Subnets are not expanded into agent lists here; the table shows their members.
- Main header: scope title ("All agents", "Unassigned", or the subnet name), a summary line ("13 agents · 11 online"), and a List / By subnet switch that sets `layout`.
- List layout: one row per agent in `scope`, filtered by `q`. Columns: Agent (avatar, name, one-line description), Status, Subnets (chips; "—" when none), Source, Room calls. Default sort is room calls, descending; sortable by name, status, and room calls. The column header has a tooltip: direct MCP and API sends are not counted.
- By subnet layout: the same rows grouped under a header per subnet (name, member count), then an "Unassigned" group. An agent in several subnets appears under each. With a subnet scope only that group shows; with `unassigned` only the Unassigned group shows. When such an agent is selected, every one of its rows is tinted (expected). Row keys combine group and agent (`<subnet id or unassigned>:<agent id>`), and only the first instance is in the Tab order to avoid repeated stops.
- Each row is one button (whole row clickable and keyboard focusable). Activating it sets `select=agent:<id>` and opens the shared details panel without changing `scope`; the selected row is tinted.
- The details panel shows only while `select` is set, and closes with its close button or Escape, so the table uses the full width by default.
- Mobile: the scope list collapses into a scope select above the list; rows become stacked cards.

This view replaces the current Agents card grid (`ConsumerAgentCard`).

#### 6.2.3 Agent detail page

Route `/network/agents/[id]`. Keeps the content and behavior of today's agent detail page (`app/(portal)/agents/[id]/page.tsx`), restyled to section 7, and adds a Subnets section.

Top to bottom:

1. Breadcrumb: Network › agent name.
2. Hero: avatar, name, status indicator, source badge (Local / Remote / Hub), "v<version> · Built by <provider>", description.
3. Actions: Chat with this agent (primary; disabled with a tooltip when the agent is inactive), Documentation (secondary, when the card has a URL), Share menu (secondary: Copy link, Share on X, LinkedIn, Reddit, native share when available), Unregister (danger, with confirmation; hidden for local agents, which keep the existing note).
4. "Try asking" quick prompts, each opening a chat with that prompt.
5. Stats: room calls and likes, when non-zero.
6. Subnets (new): each subnet the agent belongs to, with name and scope ID, linking to the Network workspace with that subnet selected. "Add to subnet" is the menu described in section 6.2. Empty state: "Not in any subnet".
7. "What this agent can help with": one card per skill with description, tags, and up to three example prompts.
8. "How this agent works" (collapsible): You can send, It returns, Capabilities.

The not-found and local-agent-unavailable states and the sticky mobile chat bar stay as today.

### 6.3 Connect (MCP and API)

Purpose: make the direction explicit: external agents or users call into this network.

- Header copy: "Connect to this network" with a one-line diagram: your agent or app -> Hybro (MCP or API) -> agents in this network.
- Tabs or segmented control: MCP / API (two routes, shared layout).
- Scope picker: "Whole network" (default) or a subnet. Scope is a per-request argument (`group_id`), never part of a connection, so it affects each page differently:
  - MCP connection configuration: unchanged by scope. It only names the server URL (`{"mcpServers": {"hybro": {"type": "http", "url": ...}}}`); MCP client configs cannot carry a subnet. A note under the snippet says scope is chosen per tool call.
  - MCP AI setup prompt: with a subnet selected, it names the subnet and its `group_id` and instructs the client to pass `group_id` on every `discover_agents` and `send_agent_message` call, and to keep the same `group_id` within a subnet, matching the MCP server instructions (which call it a team). With "Whole network", it calls `discover_agents({})` as today.
  - MCP tool-call example: a new third snippet showing `discover_agents({"group_id": "<id>"})` and a `send_agent_message` call with `group_id`, so the scope is visible without reading the prompt.
  - API request example and AI setup prompt: with a subnet selected, `GET /agents/discovery?group_id=<id>` and a `POST /agents/messages` body including `group_id`.
- `buildAccessMaterial` in `access-material.ts` takes the optional scope and produces all of the above; `buildMcpConfig` stays scope-free.
- Gateway status: existing `McpStatusControl`, restyled with the shared status indicator.
- Existing configuration / request example and AI setup prompt tabs, copy buttons, and auth help remain.

### 6.4 Rooms

No behavior change to chat. Apply the refreshed tokens, header pattern, empty and error components, and status indicator to `/chat`, room pages, and the chat-history sidebar.

Room status uses the existing room history `status` field and the shared status indicator:

| `status` | Label | Treatment |
|---|---|---|
| `idle` | (none) | No indicator. |
| `queued` | Queued | Neutral. |
| `processing` | Running | Neutral dot and label, with a small spinner only when reduced motion is off. |
| `awaiting_input` | Needs input | Warning color; the most prominent room state because it blocks progress. |

- Chat-history rows show the indicator next to the title. Rooms needing input also get a count badge on the Rooms navigation item.
- Overview shows Running and Needs input counts and lists rooms needing input under Needs attention.

### 6.5 Subnet terminology and dialog

The saved `AgentGroup` is called a Team in room UI ("Create New Team", "Saved team") and a Subnet in the Networks page. They are the same object, so the product uses one term:

- Saved group: "Subnet" everywhere, including the room group selector, its create, edit, and delete actions, and banners. Creating a subnet from a room and from the Network workspace is the same action.
- Room-scoped membership snapshot (`room_team`, currently "Room Team"): "Room agents", because it is not a saved subnet.
- One shared create and edit dialog. `NetworkFormDialog` and `GroupManagementModal` collapse into one component used by the Network workspace and the room group selector, with the same fields (name, description, agents), validation, and copy.

### 6.6 Marketing pages

Out of scope except token changes that apply globally. The new neutral gray scale and calmer dark teal (section 7) do apply to marketing pages and room chat, since they share the same tokens. Marketing pages keep `btn-brand-gradient` CTAs and their decorative backgrounds; the "logo only" gradient rule covers app pages only. Verify `/core`, `/about`, and `/pricing` visually in both themes after the token change.

## 7. Visual System

Direction: neutral and quiet. The interface is mostly grays; color carries meaning (primary action, selection, status) and nothing else.

Kept: Plus Jakarta Sans, and Hybro teal (`--color-hybro-hy`) as the single accent. Purple (`--color-hybro-bro`) and the brand gradient appear only in the logo.

| Area | Change |
|---|---|
| Neutrals | Replace the blue-tinted dark theme (`230 25% 8%` background) with near-neutral grays: page, sidebar, card, raised surface, hairline border, control border, and three text levels. Mockup dark values: page `#0e1013`, sidebar `#111317`, card `#15171c`, raised `#1b1e24`, border `#24272e`, control border `#2e323a`, text `#e4e7ec` / `#a6adb9` / `#7f8794`. The light theme gets the equivalent neutral scale. Values are mockup hex; `globals.css` stores HSL triplets, so convert them when implementing and re-check AA contrast. |
| Dark primary | Replace neon `180 100% 60%` with a calmer teal (target around `186 62% 52%`, tuned for AA on dark surfaces). |
| Accent use | Teal is used only for the primary button, the active nav icon, selection (graph highlight, selected row), subnet nodes and subnet dots, and focus rings. Links, chips, avatars, code, and charts are neutral. |
| Brand gradient | Logo only inside the app (Overview, Network, Rooms, Connect, agent pages, dialogs). No gradient buttons there. Marketing pages (section 6.6) keep their gradient CTAs. |
| Status tokens | Add `--color-status-online`, `--color-status-offline`, `--color-status-unverified`, `--color-status-warning`, `--color-status-error`. The shared status indicator is a dot (filled for online; dashed and hollow for offline and unverified) plus a neutral text label. Only Needs input colors its label (warning). No tinted status pills. Mapping in section 7.2. |
| Graph tokens | `--graph-subnet` (Hybro teal), `--graph-agent` (neutral gray), `--graph-agent-offline` (muted), `--graph-edge`, `--graph-highlight` (Hybro teal), `--graph-label`, defined for light and dark. |
| Chips and avatars | Chips (subnets, tags, skills) are neutral: gray fill or hairline outline. Agent avatars without an icon use a neutral fill with initials. The per-agent palette (`--agent-color-*`) stays for room chat only. |
| Type | Add a monospace family for IDs, URLs, and code. Fixed scale: page title (24px, semibold), section title (15px, semibold), body (14px, regular), caption (12px, regular). Section titles rely on weight, not size, to separate from body. |
| Surfaces | Three levels: page, card, panel. Hairline borders; remove card hover-lift and glow shadows. Menus and popovers use the raised surface with one soft shadow. |
| Radius | One scale derived from `--radius`: controls 8px, cards 12px, chips 6px. |

### 7.1 Buttons and clickable elements

One system, used everywhere, including dialogs and room UI.

| Variant | Look | Use |
|---|---|---|
| Primary | Solid teal, semibold; text uses `--color-primary-foreground` per theme (white on the light theme's teal, dark on the dark theme's lighter teal), meeting AA | The one main action of a view (Create subnet, Chat with this agent). At most one per view. |
| Secondary | Raised neutral fill with control border | Every other labeled action (Add agent, Manage members, Copy, Add to subnet). |
| Ghost | No fill or border, secondary text | Low-emphasis actions and section links ("All rooms ›", "Open network ›"). |
| Danger | Ghost with danger-colored text; confirms before acting | Delete subnet, Unregister. Exception: inside a destructive confirmation dialog, the confirm button is solid danger, because there the destructive action is the dialog's main action. |
| Icon | 36×36, ghost or secondary; tooltip and `aria-label` required | Close, copy, collapse, zoom, and other icon-only controls. |

- Sizes: 36px high for page and toolbar actions; 32px for actions inside cards and panels; at least 44px touch targets on mobile. Icons are 16px, 8px from the label, placed before it; a menu trigger ends with a chevron.
- Segmented controls (Topology / Structure, List / By subnet, MCP / API) share one style: bordered container, active segment on a raised neutral fill.
- Clickable rows (Needs attention, recent rooms, most-called agents, member lists, Structure rows): the whole row is one link or button with a hover fill, 8px radius, and a trailing chevron when it navigates. No separate inline text links such as "Open room" inside a row.
- Menus: raised surface, 36px items, optional one-line description per item.
- Disabled: reduced opacity, `not-allowed` cursor, and a tooltip saying why when the reason isn't obvious.

### 7.2 Status mapping

Every status in the product maps to one indicator and one token.

| Subject | State | Label | Dot | Token |
|---|---|---|---|---|
| Agent | `active` | Online | Filled | `--color-status-online` |
| Agent | `inactive`, `unavailable` | Offline | Hollow, dashed | `--color-status-offline` |
| Agent | `unknown` (not confirmed) | Unverified | Hollow, dashed | `--color-status-unverified` |
| Agent | `deleted` (still a subnet member) | Removed | Hollow, dashed | `--color-status-error` |
| Gateway (MCP) | `ready` | Ready | Filled | `--color-status-online` |
| Gateway (MCP) | `unavailable`, `unsupported_auth` | Unavailable | Hollow, dashed | `--color-status-offline` |
| Gateway (MCP, API) | check failed or timed out | Can't check | Filled | `--color-status-error` |
| Any check | in progress | Checking | Spinner (static dot with reduced motion) | `--color-status-offline` |
| Room | `queued` | Queued | Filled | `--color-status-offline` |
| Room | `processing` | Running | Filled, with spinner | `--color-status-offline` |
| Room | `awaiting_input` | Needs input | Filled; label also colored | `--color-status-warning` |
| Network data | query failed | Couldn't load | Filled | `--color-status-error` |

Error is reserved for failures (a check or query that failed, a removed agent still referenced). Offline is not an error.

## 8. Behavior Details

### 8.1 States

Every data region implements loading (skeleton matching final layout), empty (headline, one-line explanation, one action), error (what failed, Retry), and partial (data shown with an inline warning when one of several queries fails, as today's Networks page does).

### 8.2 Performance

The SVG graph targets networks up to roughly 300 nodes. Labels are hidden by default for agents partly to keep large graphs readable. Canvas rendering is not in scope.

### 8.3 Scope honesty

`group_id` is a caller-chosen scope, not server-enforced access control: a client can omit it and see all agents visible to its identity. UI copy says "Scope requests to this subnet", never "restrict access". Enforced scoped credentials are future backend work (section 10).

## 9. Testing

- Unit (Vitest): label visibility rules (including the Display switches and subnet-hover rule); URL state parsing and serialization for `view`, `q`, `scope`, `layout`, `select`, including independence, defaults, and stale IDs; `buildAccessMaterial` output per kind and scope (MCP config unchanged by scope); status mapping (section 7.2) for agents, gateway, and rooms; Overview attention-list derivation; redirects in `routes.test.ts`.
- Component: structure view scope list and table in both layouts, shared details panel for both selection types, shared subnet dialog from both entry points, toolbar actions (Create subnet; Add agent menu with Register by URL and Discover local agents), sidebar collapse and expand (icon rail, accessible names, Rooms dot), agent detail page including the Subnets section, Add to subnet menu (add, remove, create with preselection, empty state, failure with Retry), By subnet duplicate rows (keys, tint, Tab order), button variants, empty, loading, error states.
- E2E (Playwright): update `networks.spec.ts`, `portal-routing.spec.ts`, `mcp-connection.spec.ts`, and agent-link specs for the new routes; add Overview and view-switch coverage.
- Manual: light and dark themes, mobile and desktop, sidebar collapsed and expanded, keyboard-only pass, reduced motion.

## 10. Out of Scope / Future

- Gateway traffic visibility (separate follow-up spec): counting direct MCP and API sends in agent call counters, and a recent-calls feed or connected-client list (needs a backend API).
- Server-enforced subnet access (scoped credentials).
- Nested subnets.
- Room chat layout changes.

## 11. Documentation Impact

Update `frontend/docs/System-Architecture.md` routing, navigation, and Networks sections after implementation.
