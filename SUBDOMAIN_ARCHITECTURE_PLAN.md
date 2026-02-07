# HYBRO Frontend — Subdomain Architecture Implementation Plan

## Overview

Split the Hybro frontend into two distinct experiences served from a **single Next.js codebase**:

- **`hybro.ai`** — Consumer app for chatting with AI agents
- **`developer.hybro.ai`** — Developer portal for building, registering, and managing agents

The two experiences share authentication, API routes, design system, and utilities — but have completely separate layouts, navigation, and page content.

### Why

Agent **users** (consumers) and agent **builders** (developers) have fundamentally different workflows, different session patterns, and almost zero feature overlap in daily usage. The current codebase mixes both personas into one sidebar and one layout, creating confusion for both audiences. Industry standard (Shopify, Stripe, Apple, Slack, Twilio) is to separate these onto different subdomains.

---

## Target Directory Structure

```
src/
├── app/
│   ├── layout.tsx                          # Root layout (shared: Clerk, Theme, QueryProvider)
│   ├── globals.css                         # Shared design tokens & styles
│   │
│   ├── (auth)/                             # Shared auth pages (both subdomains)
│   │   ├── layout.tsx
│   │   ├── sign-in/[[...sign-in]]/page.tsx
│   │   └── sign-up/[[...sign-up]]/page.tsx
│   │
│   ├── c/                                  # Consumer routes (hybro.ai)
│   │   ├── layout.tsx                      # Consumer layout: consumer sidebar
│   │   ├── page.tsx                        # Consumer landing page (fork point)
│   │   ├── chat/page.tsx                   # New chat
│   │   ├── room/[id]/page.tsx              # Active chat room
│   │   ├── agents/page.tsx                 # Agent directory (browse, try)
│   │   ├── agents/[id]/page.tsx            # Agent profile (consumer view)
│   │   ├── about/page.tsx                  # About page
│   │   └── pricing/page.tsx                # Pricing page
│   │
│   ├── d/                                  # Developer routes (developer.hybro.ai)
│   │   ├── layout.tsx                      # Developer layout: developer sidebar
│   │   ├── page.tsx                        # Developer landing / dashboard
│   │   ├── docs/page.tsx                   # SDK documentation & code examples
│   │   ├── register/page.tsx               # Agent registration flow
│   │   ├── agents/page.tsx                 # My agents dashboard (builder view)
│   │   ├── agents/[id]/page.tsx            # Agent management (owner settings)
│   │   └── inspector/page.tsx              # A2A Agent Inspector
│   │
│   └── api/                                # Shared API routes (unchanged)
│       ├── agent/[...endpoint]/route.ts
│       ├── health/route.ts
│       ├── inspectionCenter/[...endpoint]/route.ts
│       ├── memory/[...endpoint]/route.ts
│       ├── orchestrationCenter/[...endpoint]/route.ts
│       ├── roomCenter/[...endpoint]/route.ts
│       ├── sse/[...endpoint]/route.ts
│       └── task/[...endpoint]/route.ts
│
├── components/
│   ├── ui/                                 # Shared shadcn/ui primitives (unchanged)
│   ├── providers/                          # Shared providers (unchanged)
│   ├── shared/                             # Shared business components (both subdomains)
│   │   ├── logo.tsx
│   │   ├── theme-toggle.tsx
│   │   ├── theme-provider.tsx
│   │   ├── video-embed.tsx
│   │   ├── framework-badges.tsx
│   │   ├── nav-user.tsx
│   │   └── nav-discord-button.tsx
│   ├── consumer/                           # Consumer-only components
│   │   ├── consumer-sidebar.tsx
│   │   ├── consumer-header.tsx
│   │   ├── agent-browse-card.tsx           # Consumer agent card (description, skills, "Try")
│   │   ├── room-chat-input.tsx
│   │   ├── room-messages.tsx
│   │   ├── message-bubble.tsx
│   │   ├── task-status-message.tsx
│   │   ├── group-selector.tsx
│   │   ├── group-management-modal.tsx
│   │   ├── agent-selector.tsx
│   │   ├── room-setting-form.tsx
│   │   ├── workflow-container.tsx
│   │   └── workflow-message.tsx
│   └── developer/                          # Developer-only components
│       ├── developer-sidebar.tsx
│       ├── developer-header.tsx
│       ├── agent-manage-card.tsx            # Builder agent card (status, health, "Manage")
│       ├── agent-settings-form.tsx          # Rate limits, visibility, delete
│       ├── code-block.tsx                   # Syntax-highlighted code examples
│       └── copy-button.tsx                  # Reusable copy-to-clipboard
│
├── hooks/                                  # Shared hooks (unchanged)
├── stores/                                 # Shared stores (unchanged)
└── lib/                                    # Shared utilities (unchanged, plus additions)
    ├── api/                                # All API functions (unchanged)
    ├── types/                              # All types (unchanged)
    ├── api-client.ts
    ├── auth.ts
    ├── utils.ts
    ├── consumer-nav.ts                     # NEW: consumer sidebar nav items
    ├── developer-nav.ts                    # NEW: developer sidebar nav items
    └── urls.ts                             # NEW: cross-subdomain URL helpers
```

### What Stays Unchanged

These directories are fully shared and require no changes:

- `src/lib/` — API client, auth, types, utilities
- `src/hooks/` — All custom hooks
- `src/stores/` — Zustand stores
- `src/components/ui/` — shadcn/ui primitives
- `src/components/providers/` — Clerk, React Query providers
- `src/app/api/` — All API proxy routes
- `src/app/(auth)/` — Sign-in / sign-up pages
- `src/app/layout.tsx` — Root layout (shared providers)
- `src/app/globals.css` — Shared styles

---

## Implementation Phases

### Phase 1: Middleware & Routing Foundation

**Priority:** P0 — Everything else depends on this.

**Files:** `middleware.ts`, `next.config.ts`, `.env.example`, new `lib/urls.ts`

#### 1.1 Subdomain-Routing Middleware

Replace the current Clerk-only middleware with subdomain detection + Clerk auth:

```
src/middleware.ts
```

The middleware:
- Reads the `Host` header to determine if request is on `developer.*` or `dev.*` subdomain
- Rewrites `developer.hybro.ai/register` → internal path `/d/register`
- Rewrites `hybro.ai/chat` → internal path `/c/chat`
- Skips rewriting for shared paths: `/api/*`, `/sign-in`, `/sign-up`, `/_next/*`, static files
- Skips rewriting for paths already prefixed with `/c/` or `/d/` (internal)
- Preserves Clerk middleware wrapping for auth on all routes

#### 1.2 Local Development Setup

Support subdomain detection locally without real DNS:

- `localhost:3000` → consumer experience
- `dev.localhost:3000` → developer experience (browsers resolve `*.localhost` to `127.0.0.1`)
- Fallback: `localhost:3000?_subdomain=developer` for environments where `dev.localhost` doesn't work

#### 1.3 Environment Variables

Add to `.env.example`:

```
# Subdomain URLs
NEXT_PUBLIC_CONSUMER_URL=http://localhost:3000
NEXT_PUBLIC_DEVELOPER_URL=http://dev.localhost:3000

# Clerk — same keys for both subdomains (single Clerk app)
# No CLERK_COOKIE_DOMAIN needed — Clerk handles subdomain cookies automatically in production
# No CLERK_IS_SATELLITE needed — that's only for different root domains
```

#### 1.4 Cross-Subdomain URL Helpers

Create `src/lib/urls.ts`:

- `consumerUrl(path)` — builds absolute URL to `hybro.ai` (e.g., for "Try Agents →" link)
- `developerUrl(path)` — builds absolute URL to `developer.hybro.ai` (e.g., for "Developer Portal →" link)
- Reads from `NEXT_PUBLIC_CONSUMER_URL` / `NEXT_PUBLIC_DEVELOPER_URL` env vars

#### 1.5 Verify Routing

Create minimal placeholder pages at `src/app/c/page.tsx` and `src/app/d/page.tsx` to verify the middleware correctly routes requests based on hostname. Remove placeholders after verification.

---

### Phase 2: Consumer Route Group & Layout

**Priority:** P0 — Establishes the consumer experience.

**Files:** New `c/layout.tsx`, move existing pages into `c/`

#### 2.1 Create Consumer Layout

Create `src/app/c/layout.tsx` — adapted from current `(root)/layout.tsx`:

- Uses `ConsumerSidebar` (new, adapted from `AppSidebar`)
- Uses `ConsumerHeader` (new, adapted from `Header`)
- Wraps children in `SidebarProvider` + `SidebarInset`
- Consumer-specific metadata: title "Hybro AI", description "Chat with AI agents"

#### 2.2 Create Consumer Sidebar

Create `src/components/consumer/consumer-sidebar.tsx` — adapted from current `app-sidebar.tsx`:

- Navigation items: **New Chat** (`/chat`), **Explore Agents** (`/agents`)
- History section: room list (same as current)
- Footer: Discord, User menu
- Remove: "Developers", "Register Agent", "Upgrade" button
- Add: "Developer Portal →" link in footer (uses `developerUrl('/')`)

#### 2.3 Create Consumer Nav Config

Create `src/lib/consumer-nav.ts`:

```
New Chat       /chat         MessageCirclePlus
Explore Agents /agents       Globe
```

#### 2.4 Move Consumer Pages

Move existing pages into `src/app/c/`:

| Source | Destination | Changes |
|--------|------------|---------|
| `(root)/(home)/page.tsx` | `c/page.tsx` | Update CTAs for bifurcated landing |
| `(root)/chat/page.tsx` | `c/chat/page.tsx` | None |
| `(root)/room/[id]/page.tsx` | `c/room/[id]/page.tsx` | None |
| `(root)/about/page.tsx` | `c/about/page.tsx` | None |
| `(root)/price/page.tsx` | `c/pricing/page.tsx` | None |

#### 2.5 Create Consumer Agent Pages (from existing)

| Source | Destination | Changes |
|--------|------------|---------|
| `(root)/agent/page.tsx` | `c/agents/page.tsx` | Remove "My Agents" tab, remove "Register Agent" CTA |
| `(root)/agent/profile/[id]/page.tsx` | `c/agents/[id]/page.tsx` | Remove owner settings section; add "Chat with this agent" CTA |

---

### Phase 3: Developer Route Group & Layout

**Priority:** P0 — Establishes the developer experience.

**Files:** New `d/layout.tsx`, move/create developer pages in `d/`

#### 3.1 Create Developer Layout

Create `src/app/d/layout.tsx`:

- Uses `DeveloperSidebar` (new)
- Uses `DeveloperHeader` (new)
- Developer-specific metadata: title "HYBRO Developers", description "Build interoperable AI agents"
- Same `SidebarProvider` + `SidebarInset` pattern

#### 3.2 Create Developer Sidebar

Create `src/components/developer/developer-sidebar.tsx`:

- Navigation items: **Dashboard** (`/`), **Register Agent** (`/register`), **Inspector** (`/inspector`), **Docs & SDK** (`/docs`)
- "My Agents" section: list of user's registered agents (like History in consumer sidebar, but shows agents instead of rooms)
- Footer: Discord, User menu
- Add: "Try Agents →" link in footer (uses `consumerUrl('/chat')`)

#### 3.3 Create Developer Nav Config

Create `src/lib/developer-nav.ts`:

```
Dashboard       /              LayoutDashboard
Register Agent  /register      ClipboardList
Inspector       /inspector     Shield
Docs & SDK      /docs          Code2
```

#### 3.4 Move Developer Pages

| Source | Destination | Changes |
|--------|------------|---------|
| `(root)/developers/page.tsx` | `d/docs/page.tsx` | None (or merge with developer landing) |
| `(root)/agent/registry/page.tsx` | `d/register/page.tsx` | None |
| `(root)/inspector/page.tsx` | `d/inspector/page.tsx` | None |

#### 3.5 Create New Developer Pages

| Page | Purpose |
|------|---------|
| `d/page.tsx` | Developer landing: SDK hero + install command (unauth) / dashboard with agent stats (auth) |
| `d/agents/page.tsx` | "My Agents" dashboard: table/grid of owned agents with status, health, request count |
| `d/agents/[id]/page.tsx` | Agent management: owner settings (rate limits, visibility, delete), analytics, "View as User →" link |

`d/page.tsx` should be auth-aware:
- **Unauthenticated**: Show SDK intro (adapted from current `developers/page.tsx` hero section)
- **Authenticated**: Show builder dashboard with agent count, recent registrations, quick actions

`d/agents/page.tsx` is derived from the "My Agents" tab of the current `agent/page.tsx`.

`d/agents/[id]/page.tsx` is derived from the owner-only sections of current `agent/profile/[id]/page.tsx`.

---

### Phase 4: Cleanup Old Routes & Components

**Priority:** P1 — Remove the old structure once consumer + developer routes are working.

#### 4.1 Delete Old Route Group

Remove the entire `src/app/(root)/` directory:

- `(root)/layout.tsx` — replaced by `c/layout.tsx` and `d/layout.tsx`
- `(root)/(home)/page.tsx` — replaced by `c/page.tsx`
- `(root)/chat/page.tsx` — moved to `c/chat/page.tsx`
- `(root)/room/[id]/page.tsx` — moved to `c/room/[id]/page.tsx`
- `(root)/agent/page.tsx` — split into `c/agents/page.tsx` and `d/agents/page.tsx`
- `(root)/agent/profile/[id]/page.tsx` — split into `c/agents/[id]/page.tsx` and `d/agents/[id]/page.tsx`
- `(root)/agent/registry/page.tsx` — moved to `d/register/page.tsx`
- `(root)/developers/page.tsx` — moved to `d/docs/page.tsx`
- `(root)/inspector/page.tsx` — moved to `d/inspector/page.tsx`
- `(root)/about/page.tsx` — moved to `c/about/page.tsx`
- `(root)/price/page.tsx` — moved to `c/pricing/page.tsx`
- `(root)/workspace/page.tsx` — delete (was already a dead redirect)

#### 4.2 Reorganize Components

Move existing components into the new structure:

**Move to `components/shared/`:**
- `logo.tsx`, `theme-toggle.tsx`, `theme-provider.tsx`
- `video-embed.tsx`, `framework-badges.tsx`
- `nav-user.tsx`, `nav-discord-button.tsx`

**Move to `components/consumer/`:**
- `room-chat-input.tsx`, `room-messages.tsx`, `message-bubble.tsx`
- `task-status-message.tsx`, `group-selector.tsx`, `group-management-modal.tsx`
- `agent-selector.tsx`, `room-setting-form.tsx`
- `workflow-container.tsx`, `workflow-message.tsx`

**Move to `components/developer/`:**
- Extract `CopyButton` and `CodeBlock` from `developers/page.tsx` into `developer/copy-button.tsx` and `developer/code-block.tsx`

**Delete:**
- `app-sidebar.tsx` — replaced by `consumer/consumer-sidebar.tsx` and `developer/developer-sidebar.tsx`
- `header.tsx` — replaced by `consumer/consumer-header.tsx` and `developer/developer-header.tsx`
- `nav-agent.tsx` — functionality absorbed into new sidebar components
- `nav-main.tsx` — functionality absorbed into new sidebar components
- `nav-items.ts` — replaced by `consumer-nav.ts` and `developer-nav.ts`
- `upgrade-button.tsx` — remove or move to consumer only (pricing is consumer-side)
- `agent-card.tsx` — replaced by `consumer/agent-browse-card.tsx` and `developer/agent-manage-card.tsx`

#### 4.3 Update Imports

After moving files, update all import paths. Use IDE find-and-replace for:
- `@/components/app-sidebar` → `@/components/consumer/consumer-sidebar` or `@/components/developer/developer-sidebar`
- `@/components/header` → `@/components/consumer/consumer-header` or `@/components/developer/developer-header`
- `@/components/agent-card` → `@/components/consumer/agent-browse-card` or `@/components/developer/agent-manage-card`
- `@/lib/nav-items` → `@/lib/consumer-nav` or `@/lib/developer-nav`
- All component moves from root `components/` to `components/shared/`, `components/consumer/`, or `components/developer/`

---

### Phase 5: Consumer Landing Page Redesign

**Priority:** P1 — The landing page is the fork point between the two experiences.

**Files:** `c/page.tsx`

Redesign the consumer landing page with a clear bifurcation:

1. **Hero section** — HYBRO branding + tagline
2. **Two-path fork** — Two visually equal cards:
   - **"Use Agents"** card: "Chat with AI agents that collaborate" → CTA: "Start Chatting" (leads to `/chat` or sign-up)
   - **"Build & Deploy"** card: "Make your agent interoperable in 3 lines" → CTA: "Developer Portal →" (leads to `developer.hybro.ai`)
3. **Demo video** — showing the chat experience
4. **How it works** (consumer version): Ask a question → Agents collaborate → Get answers
5. **Agent showcase** — featured agents from the network (optional, can defer)
6. **Footer** — links, copyright

Key change from current: Remove `pip install a2a-adapter`, framework names, and builder-focused "How it Works" steps (Adapt/Connect/Collaborate). Those move to the developer landing page.

---

### Phase 6: Developer Landing & Dashboard Page

**Priority:** P1 — The entry point for the builder community.

**Files:** `d/page.tsx`

Auth-aware developer landing:

**Unauthenticated view:**
1. **Hero** — "Build interoperable AI agents" + `pip install a2a-adapter` + copy button
2. **Action buttons** — GitHub, Documentation, PyPI
3. **Demo video** — showing the builder flow
4. **How it works** (builder version): Wrap agent → Start server → Register on HYBRO
5. **Supported frameworks** — `FrameworkBadges` component
6. **Code examples** — Quick Start, CrewAI, LangGraph (tabbed)
7. **Next steps** — Test (Inspector) → Register → Try Chat
8. **Resources** — GitHub, Discord, email

**Authenticated view:**
1. **Dashboard header** — "Welcome back" + quick stats (agent count, total requests)
2. **Quick actions** — Register new agent, Open Inspector, View Docs
3. **My Agents summary** — list of registered agents with status indicators
4. **Recent activity** — latest registrations or status changes

---

### Phase 7: Shared Auth Across Subdomains

**Priority:** P1 — Required for production deployment.

**Files:** `.env`, `middleware.ts`, Clerk dashboard configuration

#### 7.1 Clerk Subdomain Auth (Works Automatically)

Clerk **natively supports authentication across subdomains** when the root domain is set in the Clerk Dashboard. No multi-domain or satellite domain configuration is needed.

When the Clerk production root domain is set to `hybro.ai`, Clerk automatically sets session cookies on `.hybro.ai`, which are accessible to all subdomains including `developer.hybro.ai`. Both subdomains use the **same Clerk application** (same Publishable Key and Secret Key).

**What this means:**
- A user who signs in on `hybro.ai` is automatically signed in on `developer.hybro.ai` (and vice versa)
- No `CLERK_COOKIE_DOMAIN` env var is needed — Clerk handles this internally
- No satellite domain setup is needed — that feature is only for **different root domains** (e.g., `hybro.ai` + `hybro-dev.com`)
- No `<ClerkProvider isSatellite>` configuration is needed

**What you DO need:**
1. Set the root domain to `hybro.ai` in the Clerk Dashboard (Domains page) for your production instance
2. Add the required Clerk DNS records (CNAME for `clerk.hybro.ai` or similar) as shown in the Dashboard
3. Configure `authorizedParties` in middleware for security (see 7.2)

> **Reference:** [Clerk docs — Authentication across subdomains](https://clerk.com/docs/guides/development/deployment/production#authentication-across-subdomains)

#### 7.2 Configure `authorizedParties` (Security)

Set `authorizedParties` in `clerkMiddleware()` to protect against subdomain cookie leaking attacks. Without this, a compromised app on another subdomain of `hybro.ai` could generate valid sessions.

```typescript
// middleware.ts
import { clerkMiddleware } from '@clerk/nextjs/server';

export default clerkMiddleware({
  authorizedParties: [
    'https://hybro.ai',
    'https://developer.hybro.ai',
  ],
});
```

#### 7.3 Production Environment Variables

```
# Subdomain URLs
NEXT_PUBLIC_CONSUMER_URL=https://hybro.ai
NEXT_PUBLIC_DEVELOPER_URL=https://developer.hybro.ai

# Clerk (same keys for both subdomains — it's one app)
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_live_...
CLERK_SECRET_KEY=sk_live_...
NEXT_PUBLIC_CLERK_SIGN_IN_URL=/sign-in
NEXT_PUBLIC_CLERK_SIGN_UP_URL=/sign-up
```

No `CLERK_COOKIE_DOMAIN`, `CLERK_IS_SATELLITE`, or `CLERK_DOMAIN` env vars are needed.

#### 7.4 DNS & Deployment

- Both `hybro.ai` and `developer.hybro.ai` point to the **same** deployment (single Next.js app)
- If using **Vercel**: add both `hybro.ai` and `developer.hybro.ai` as domains in project settings → both map to the same deployment. Set DNS on GoDaddy:
  - `hybro.ai` → A record to `76.76.21.21` (or CNAME to `cname.vercel-dns.com`)
  - `developer.hybro.ai` → CNAME to `cname.vercel-dns.com`
- If using a **VPS**: both domains → A record to the same server IP; reverse proxy (Nginx/Caddy) forwards both to the single Next.js process
- Add Clerk's required DNS records (shown in Clerk Dashboard → Domains) for session management
- Verify SSL covers both domains (Vercel handles this automatically; for VPS use Caddy or Let's Encrypt)

---

### Phase 8: UX Improvements (Consumer)

**Priority:** P2 — Polish the consumer experience after the split.

#### 8.1 Richer Agent Browse Cards

Replace the current square name-only `AgentCard` with `agent-browse-card.tsx`:
- Rectangular card layout
- Shows: name, one-line description, skill tags, framework badge, status
- Primary CTA: "Chat with this agent" button
- Secondary: click card → agent profile

#### 8.2 Agent Profile Consumer View

Clean up `c/agents/[id]/page.tsx`:
- Remove all owner settings (rate limits, visibility, delete)
- Add prominent "Chat with this agent" CTA at the top
- Show: description, capabilities, skills, interaction modes
- Add "Built by [provider]" attribution
- Add "View on Developer Portal →" link for agents the user owns

#### 8.3 Simplify Chat Input

Reduce visual complexity of `RoomChatInput`:
- Hide group selector behind a settings/filter icon by default
- Simplify the input border/glow effects
- Keep @mention functionality

---

### Phase 9: UX Improvements (Developer)

**Priority:** P2 — Polish the developer experience after the split.

#### 9.1 Builder Agent Dashboard

Build `d/agents/page.tsx` as a proper management dashboard:
- Table or list view (not card grid)
- Columns: agent name, status (active/inactive), registered date, provider
- Row actions: Manage, View as User
- "Register New Agent" CTA at the top

#### 9.2 Agent Management Page

Build `d/agents/[id]/page.tsx` with:
- Agent info summary (name, URL, status)
- Settings: visibility (public/private), rate limits
- Danger zone: delete with confirmation
- "View as User →" link (opens `consumerUrl('/agents/[id]')` in new tab)

#### 9.3 Code Block Syntax Highlighting

Add proper syntax highlighting to code examples on the docs page. The project already has `rehype-highlight` installed — use it in the `CodeBlock` component.

---

## Migration Mapping

### Pages: Current → New

| Current Path | Current File | Consumer (`c/`) | Developer (`d/`) |
|---|---|---|---|
| `/` | `(root)/(home)/page.tsx` | `c/page.tsx` (redesigned landing) | — |
| `/chat` | `(root)/chat/page.tsx` | `c/chat/page.tsx` | — |
| `/room/[id]` | `(root)/room/[id]/page.tsx` | `c/room/[id]/page.tsx` | — |
| `/agent` | `(root)/agent/page.tsx` | `c/agents/page.tsx` (browse only) | `d/agents/page.tsx` (my agents) |
| `/agent/profile/[id]` | `(root)/agent/profile/[id]/page.tsx` | `c/agents/[id]/page.tsx` (read only) | `d/agents/[id]/page.tsx` (manage) |
| `/agent/registry` | `(root)/agent/registry/page.tsx` | — | `d/register/page.tsx` |
| `/developers` | `(root)/developers/page.tsx` | — | `d/docs/page.tsx` |
| `/inspector` | `(root)/inspector/page.tsx` | — | `d/inspector/page.tsx` |
| `/about` | `(root)/about/page.tsx` | `c/about/page.tsx` | — |
| `/price` | `(root)/price/page.tsx` | `c/pricing/page.tsx` | — |
| `/workspace` | `(root)/workspace/page.tsx` | Delete | Delete |
| — | — | — | `d/page.tsx` (NEW: developer landing/dashboard) |

### Components: Current → New

| Current Component | New Location | Notes |
|---|---|---|
| `app-sidebar.tsx` | Split into `consumer/consumer-sidebar.tsx` + `developer/developer-sidebar.tsx` | Different nav items, different history sections |
| `header.tsx` | Split into `consumer/consumer-header.tsx` + `developer/developer-header.tsx` | Different branding/context |
| `agent-card.tsx` | Split into `consumer/agent-browse-card.tsx` + `developer/agent-manage-card.tsx` | Different info displayed |
| `nav-agent.tsx` | Absorbed into new sidebar components | No longer needed as separate |
| `nav-main.tsx` | Absorbed into new sidebar components | No longer needed as separate |
| `nav-items.ts` | Split into `consumer-nav.ts` + `developer-nav.ts` | Different nav configs |
| `logo.tsx` | `shared/logo.tsx` | Unchanged |
| `nav-user.tsx` | `shared/nav-user.tsx` | Unchanged |
| `nav-discord-button.tsx` | `shared/nav-discord-button.tsx` | Unchanged |
| `video-embed.tsx` | `shared/video-embed.tsx` | Unchanged |
| `framework-badges.tsx` | `shared/framework-badges.tsx` | Unchanged |
| `theme-toggle.tsx` | `shared/theme-toggle.tsx` | Unchanged |
| `theme-provider.tsx` | `shared/theme-provider.tsx` | Unchanged |
| `upgrade-button.tsx` | Delete or `consumer/` only | Pricing is consumer-side |
| All room/chat components | `consumer/` | Only used in consumer app |
| All workflow components | `consumer/` | Only used in consumer app |

---

## Implementation Order

```
Phase 1: Middleware & routing foundation                          ✅ DONE
Phase 2: Consumer route group & layout                           ✅ DONE
Phase 3: Developer route group & layout                          ✅ DONE
Phase 4: Cleanup old routes & components                         ✅ DONE
Phase 5: Consumer landing page redesign                          ✅ DONE
Phase 6: Developer landing & dashboard page                      ✅ DONE
Phase 7: Shared auth across subdomains                           ✅ DONE
Phase 8: UX improvements (consumer)                              ⬜ TODO
Phase 9: UX improvements (developer)                             ⬜ TODO
```

Phases 1–4 are the structural migration (can be done in a single focused sprint).
Phases 5–6 are the new page designs.
Phase 7 is production deployment config.
Phases 8–9 are polish and can be done incrementally.

---

## Deferred (Do Later)

- **Consumer agent showcase** on landing page (featured/trending agents)
- **Builder analytics** (request counts, error logs per agent)
- **Embedded inspector** (inline A2A testing instead of external link)
- **Pricing page functionality** (wire up plan selection buttons)
- **Full API reference docs** (when a2a-adapter publishes auto-generated docs)
- **Blog / changelog** on developer portal (community content pipeline)
