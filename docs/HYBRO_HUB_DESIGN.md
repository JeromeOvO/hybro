# Hybro Hub: Portal-First Hybrid Agent Architecture

**Status:** Draft v3.6
**Date:** 2026-03-07
**Author:** Architecture Design
**A2A Protocol Version:** v0.3 (current SDK: `a2a-sdk >=0.3, <1.0`)

---

## Table of Contents

1. [Vision & Principles](#1-vision--principles)
2. [Architecture Overview](#2-architecture-overview)
3. [Hybro Hub (Local Daemon)](#3-hybro-hub-local-daemon)
4. [Cloud Gateway API](#4-cloud-gateway-api)
5. [Cloud Relay & Agent Sync](#5-cloud-relay--agent-sync)
6. [Privacy & Data Routing](#6-privacy--data-routing)
7. [Portal-First Web UI](#7-portal-first-web-ui)
8. [Protocol & Compatibility](#8-protocol--compatibility)
9. [A2A Version Strategy](#9-a2a-version-strategy)
10. [User Journey](#10-user-journey)
11. [Phased Implementation Roadmap](#11-phased-implementation-roadmap)
12. [Competitive Landscape](#12-competitive-landscape)
13. [Risks & Mitigations](#13-risks--mitigations)
14. [Open Questions](#14-open-questions)
15. [Cross-Document Integration Notes](#15-cross-document-integration-notes)

---

## 1. Vision & Principles

### 1.1 The Opportunity

The AI agent landscape is splitting into two camps:

- **Cloud-first** platforms (Cursor Cloud Agents, OpenAI Codex Cloud, Devin) push all
  execution to remote infrastructure. They offer power and convenience but require
  users to surrender their data to third-party servers.
- **Local-first** runtimes (Ollama, LM Studio, Open Interpreter, Pryx) keep
  everything on the user's machine. They offer privacy and control but lack access
  to the broader ecosystem of specialized cloud agents.

Neither camp serves users who want **both**: the privacy and control of local
execution with the power and scalability of cloud agent ecosystems.

hybro.ai will be the bridge — a platform where users open one web portal, see
both their local agents and cloud agents side by side, and choose where their
data goes.

### 1.2 Product Thesis

> Open hybro.ai. See your local agents alongside cloud agents. Chat with any of
> them. Your local agents run on your machine — your data stays private. Cloud
> agents give you more power when you need it. One portal, your choice.

### 1.3 Design Principles

| # | Principle | Meaning |
|---|-----------|---------|
| P1 | **Portal-first** | The user's primary interface is always `hybro.ai`. Local agents appear alongside cloud agents in the same web portal. |
| P2 | **Privacy by architecture** | The hub controls what data leaves the machine. Local agent processing stays local. |
| P3 | **Outbound-only connections** | The hub never accepts inbound connections from the internet. All cloud communication is initiated by the hub. |
| P4 | **A2A everywhere** | Local agents, cloud agents, and the hub itself all speak the A2A protocol. |
| P5 | **Invisible infrastructure** | The hub is a background daemon the user rarely interacts with directly. Setup once, forget about it. |
| P6 | **Graceful degradation** | If the hub is offline, cloud agents still work. Local agents show as "offline" — no broken UX. |
| P7 | **Progressive enhancement** | Users without a hub use hybro.ai as today (cloud-only). Installing a hub adds local agents to the same portal. |

### 1.4 User Personas

**Developer (primary, Phase 1 target)**
- Runs local LLMs via Ollama, builds custom A2A agents
- Wants to combine their local agents with cloud specialists (e.g., a legal agent, a code review agent)
- Comfortable with CLI, config files, `pip install`
- Cares about: API keys staying local, code not leaving their machine

**Privacy-conscious professional (Phase 2 target)**
- Uses AI daily but is concerned about sending sensitive documents to cloud APIs
- Wants: "my medical/legal/financial data stays on my laptop, but I can still use powerful AI agents"
- Needs a simple desktop app, not a terminal
- Cares about: clear privacy indicators, zero-config local setup

**Enterprise team (Phase 3 target)**
- Deploys agents on internal infrastructure
- Needs: data sovereignty, audit trails, SSO, compliance certifications
- Wants to use hybro.ai's agent marketplace without sending proprietary data to the cloud

---

## 2. Architecture Overview

### 2.1 System Topology

```
┌──────────────────────────────────────────────────────────────────┐
│  User's Machine / Local Network                                  │
│                                                                  │
│  ┌────────────────────────────────────────────────────────┐      │
│  │  Hybro Hub (background daemon)                         │      │
│  │                                                        │      │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────────┐ │      │
│  │  │ Local   │ │ Local   │ │ Local   │ │ User's      │ │      │
│  │  │ Agent A │ │ Agent B │ │ LLM     │ │ MCP Servers │ │      │
│  │  │ (A2A)   │ │ (A2A)   │ │(Ollama) │ │ (tools)     │ │      │
│  │  └────┬────┘ └────┬────┘ └────┬────┘ └──────┬──────┘ │      │
│  │       └───────────┴───────────┴──────────────┘        │      │
│  │                        │                              │      │
│  │  ┌─────────────────────▼─────────────────────────┐    │      │
│  │  │  Orchestrator + Privacy Router                │    │      │
│  │  └──────────┬────────────────────┬───────────────┘    │      │
│  │             │                    │                    │      │
│  │  ┌──────────▼──────────┐  ┌─────▼──────────────┐     │      │
│  │  │ Relay Client        │  │ Gateway Client     │     │      │
│  │  │ (SSE subscribe +    │  │ (call cloud agents │     │      │
│  │  │  event publish)     │  │  via gateway API)  │     │      │
│  │  └──────────┬──────────┘  └─────┬──────────────┘     │      │
│  └─────────────┼───────────────────┼────────────────────┘      │
│                │                   │                            │
└────────────────┼───────────────────┼────────────────────────────┘
                 │  Outbound HTTPS   │
                 ▼                   ▼
┌──────────────────────────────────────────────────────────────────┐
│  hybro.ai Cloud                                                  │
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐   │
│  │ Hub Relay    │  │ Gateway API  │  │ Agent Marketplace    │   │
│  │ Service      │  │              │  │                      │   │
│  │ - Agent sync │  │ - Discovery  │  │ - Browse & register  │   │
│  │ - Message    │  │ - A2A Proxy  │  │ - Cloud agents       │   │
│  │   relay      │  │ - Rate limit │  │ - Local agents       │   │
│  │ - Status     │  │              │  │   (from user's hub)  │   │
│  └──────┬───────┘  └──────┬───────┘  └──────────────────────┘   │
│         │                 │                                      │
│  ┌──────▼─────────────────▼──────────────────────────────────┐   │
│  │  Existing Backend (rooms, messages, SSE, orchestration)   │   │
│  └───────────────────────────────────────────────────────────┘   │
│                              │                                   │
│  ┌───────────────────────────▼──────────────────────────────┐    │
│  │  Remote A2A Agents (third-party cloud agents)            │    │
│  └──────────────────────────────────────────────────────────┘    │
│                                                                  │
│  ┌────────────┐                                                  │
│  │ Browser    │ ← connects to hybro.ai (always)                 │
│  │ (hybro.ai) │    sees local + cloud agents in one list        │
│  └────────────┘                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### 2.2 Data Flow Modes

**Mode 1: Cloud-only (no hub — current hybro.ai, unchanged)**

```
Browser → hybro.ai → Cloud Orchestrator → Cloud Agents
```

Users without a hub use hybro.ai exactly as it works today. This is the default
experience and requires no changes.

**Mode 2: Portal + Hub (primary hybrid mode)**

```
Browser → hybro.ai ─→ Hub Relay ─→ Hub Daemon ─→ Local Agent(s)
                    ─→ Cloud Orchestrator ─→ Cloud Agent(s)
```

User sees both local and cloud agents on hybro.ai. Messages to local agents
route through the relay to the hub. Messages to cloud agents route through
the existing cloud orchestrator. The user doesn't switch modes — routing
happens transparently based on which agent they're talking to.

**Mode 3: Air-gapped (power-user escape hatch)**

```
Browser → localhost:9000 → Hub Daemon → Local Agents only
```

For users who refuse any cloud connectivity. The hub optionally serves a
minimal status page on localhost. This is not the primary UX — it's a
fallback for security hardliners and air-gapped environments.

### 2.3 Key Architectural Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Primary UI | `hybro.ai` web portal (always) | One URL, one auth system, no mode switching. Local agents appear as "virtual agents" in the cloud DB. |
| Hub role | Background daemon (not a web server) | Hub receives tasks via relay, dispatches to local agents, publishes results. No local API surface needed. |
| Hub runtime language | Python | Matches existing backend. Maximizes code sharing for A2A client and privacy router. |
| Hub distribution | `pip install hybro-hub` + standalone binary | Developers use pip. Non-technical users download a binary. |
| Local storage | Minimal — cryptographic keys, cached policies, cached tokens | Rooms, messages, and history live in the cloud (MongoDB). Hub stores only `~/.hybro/keys/` (identity keys), cached trust policies, and cached tokens. See HYBRO_TRUST_LAYER_DESIGN.md §7.1. |
| Hub ↔ Cloud connection | Outbound HTTPS only (SSE subscribe + REST post) | No inbound ports, works behind NAT/firewalls. Same pattern as Clarifai Local Runners and Home Assistant. |
| Agent protocol | A2A (JSON-RPC over HTTP) | Already used by hybro.ai. Agents work identically whether local or remote. |
| Tool protocol | MCP (for local tools) | Industry standard. Hub can host MCP servers that local agents use. |

---

## 3. Hybro Hub (Local Daemon)

The hub is a lightweight background process that bridges local agents to the
hybro.ai cloud. It is **not** a web server, does not serve a UI, and does not
store rooms or messages. It is an orchestration daemon.

### 3.1 Component Structure

```
hybro-hub/
├── hub/
│   ├── __init__.py             # Package init
│   ├── __main__.py             # `python -m hub` entry point
│   ├── main.py                 # HubDaemon: startup, relay connection, event loop
│   ├── config.py               # YAML config loader + env vars + hub_id persistence
│   ├── relay_client.py         # SSE subscription + HTTP publish to hybro.ai relay
│   ├── agent_registry.py       # Discover and health-check local A2A agents
│   ├── dispatcher.py           # A2A client — dispatch tasks to local agents
│   ├── privacy_router.py       # Sensitivity classification (keyword + regex)
│   ├── cli.py                  # Click CLI: start, status, agents, agent start
│   └── gateway_client.py       # (Phase 3) Call cloud agents via gateway API
├── config.yaml.example         # Annotated example configuration
├── pyproject.toml              # Package metadata, dependencies, entry points
└── tests/
    ├── test_relay_client.py
    ├── test_agent_registry.py
    ├── test_dispatcher.py
    └── test_privacy_router.py

a2a-adapter/                    # Separate repo — framework-to-A2A adapters
└── a2a_adapter/
    └── integrations/
        └── ollama.py           # OllamaAdapter: wraps Ollama HTTP API as A2A agent
```

### 3.2 Configuration

```yaml
# ~/.hybro/config.yaml

# Local agents: A2A agents running on this machine or LAN
agents:
  local:
    - name: "My Code Reviewer"
      url: "http://localhost:8001"
    - name: "Document Analyzer"
      url: "http://localhost:8002"
    - name: "Team Agent"
      url: "http://192.168.1.50:8080"   # LAN agent
  auto_discover: true                    # Scan localhost ports for agent cards

# hybro.ai cloud connection (required for portal-first mode)
cloud:
  api_key: "hba_..."                     # hybro.ai API key
  gateway_url: "https://api.hybro.ai"

# Privacy policy: controls what data can leave the machine
privacy:
  default_routing: "local_first"         # "local_first" | "cloud_first" | "local_only"
  sensitive_keywords: ["medical", "legal", "financial", "password", "ssn"]
  never_send_to_cloud:
    - file_contents
    - personal_identifiers
```

### 3.3 Orchestrator

The orchestrator receives user messages (via the relay) and decides how to
handle them. It is a **new, standalone implementation** — not a fork of the
backend's `SupervisorExecutor`, which has deep dependencies on MongoDB,
Pinecone, SSEManager, and the full ResponseProcessor chain.

The hub orchestrator is simpler: it classifies sensitivity, picks an agent
(local or cloud), dispatches via A2A, and publishes the result back through
the relay. No debate mode, no queue execution, no workflow engine.

**Decision flow:**

```
User Message (from relay SSE)
  │
  ▼
┌─────────────────────────────┐
│ Privacy Router (§6)         │
│ Classify sensitivity        │
└──────────────┬──────────────┘
               │
       ┌───────┴───────┐
       ▼               ▼
   HIGH sensitivity   LOW sensitivity
   (local only)       (local or cloud)
       │               │
       ▼               ▼
   Dispatch to      Dispatch to best
   local agent      agent (any location)
       │               │
       └───────┬───────┘
               ▼
   Collect response
   Publish to relay → hybro.ai → browser SSE
```

### 3.4 Agent Discovery

The hub finds local agents via:
1. **Manual config**: agents listed in `config.yaml`
2. **Auto-discovery**: uses `psutil` to enumerate all TCP ports listening on
   localhost, then probes each for A2A agent cards at both
   `/.well-known/agent-card.json` (current) and `/.well-known/agent.json`
   (deprecated), using the canonical paths from the `a2a-python` SDK.
   Well-known non-agent ports (SSH, databases, etc.) are excluded via
   `auto_discover_exclude_ports`. Probes are concurrency-limited (30 at a time).
3. **Health checks**: periodic ping to verify agents are still running

On startup and whenever the agent list changes, the hub syncs its agent
catalog to hybro.ai via `POST /api/v1/relay/hub/{hub_id}/agents/sync`.

### 3.5 Distribution

| Target | Method | Size |
|--------|--------|------|
| Developers | `pip install hybro-hub` (PyPI) | ~5 MB |
| macOS users | Homebrew: `brew install hybro-hub` | ~10 MB |
| Windows/Linux | Standalone binary via PyInstaller | ~25 MB |
| Desktop app (Phase 3) | Tauri wrapper with system tray | ~20 MB |
| Docker | `docker run hybro/hub` | ~80 MB |

The hub starts in <2 seconds and uses <50 MB RAM at idle.

---

## 4. Cloud Gateway API

The Gateway API lets the hub call cloud agents without knowing their real URLs.
It is a thin authenticated proxy built on existing backend services.

### 4.1 Endpoints

All gateway endpoints require `X-API-Key` authentication. The API key maps
to a `user_id` via the existing `APIKey` model (`models/api_key.py`).

| Endpoint | Implementation |
|----------|---------------|
| `POST /api/v1/gateway/agents/discover` | Wraps `DiscoveryService.discover_agents()`, resolves `agent_id`s, masks URLs |
| `POST /api/v1/gateway/agents/{id}/message/send` | Wraps `A2AService.send_message_sync()` |
| `POST /api/v1/gateway/agents/{id}/message/stream` | Wraps `A2AService.send_message()` (auto-detects streaming capability) |
| `GET /api/v1/gateway/agents/{id}/card` | Returns `AgentCard` from MongoDB with gateway-masked URL |

### 4.2 Security

- **Authentication:** API key validated via `common/api_key_auth.py`. The
  `APIKey` model already has a `user_id` field.
- **Access control:** Gateway enforces `agent_status == active` and
  `(is_public == True OR api_key.user_id == agent.provider_id)` before
  allowing any agent interaction.
- **Agent URL masking:** The hub only sees `agent_id`. The gateway resolves
  the real URL internally, protecting agent providers.
- **Response URL rewriting:** A2A responses from cloud agents may contain
  self-referential URLs (e.g., `AgentCard.url` in embedded task data, webhook
  callback URLs). The gateway must rewrite these to gateway-relative URLs
  before returning to the hub, preventing the hub from bypassing the gateway
  on subsequent requests.
- **Rate limiting:** Two layers:
  - *Gateway-level:* Per-key and global rate limits via `GatewayRateLimitService`
    (backed by `gateway_api_requests` MongoDB collection with TTL index).
  - *Agent-level:* Per-user and system-wide per-agent limits via the existing
    `RateLimitService`.
- **CORS:** The `DiscoveryCORSMiddleware` applies permissive CORS headers to
  both `/discovery` and `/gateway` paths for external SDK access.
- **Streaming auth:** Authentication and access control are validated *before*
  the SSE stream starts. If checks fail, the client receives a proper HTTP
  error status (401/403/404/429), not an SSE error event.

### 4.3 Relationship to Existing Backend

The gateway adds **new API routes** to `multi-agents-backend`. It reuses
`A2AService`, `DiscoveryService`, `AgentService`, `RateLimitService`, and
`api_key_auth`. No changes to the orchestration layer are needed — the
gateway is a proxy, not an orchestrator.

**Files added/modified:**

| File | Role |
|------|------|
| `api/gateway.py` | Router — 4 endpoints (discover, send, stream, card) |
| `services/gateway_service.py` | Business logic — agent lookup, access control, URL masking, usage tracking |
| `services/gateway_rate_limit_service.py` | Per-key and global rate limiting for gateway requests |
| `database/migration/add_gateway_api_requests_indexes.py` | TTL + query indexes for rate limit collection |
| `config/settings.py` | Added `gateway_base_url`, `gateway_rate_limit_per_key`, `gateway_rate_limit_global` |
| `database/mongodb.py` | Added `gateway_api_requests_collection` property and `increment_agent_call_count()` |
| `common/middleware/discovery_cors_middleware.py` | Extended to cover `/gateway` paths |
| `main.py` | Mounted gateway router |

---

## 5. Cloud Relay & Agent Sync

The relay is the **primary integration path** between the hub and hybro.ai.
It handles three things: agent registration, message routing, and status.

### 5.1 Agent Sync

When the hub connects, it registers its local agents as "virtual agents" in
the cloud database.

```
POST /api/v1/relay/hub/{hub_id}/agents/sync
X-API-Key: hba_...

Request:
{
  "agents": [
    {
      "local_agent_id": "local_001",
      "name": "My Ollama Chat",
      "description": "Local LLM via Ollama (llama3.2:8b)",
      "capabilities": ["chat", "code"],
      "agent_card": { ... }            // Standard A2A AgentCard
    }
  ]
}
```

The backend upserts these as `Agent` records in MongoDB with new fields:

```python
class Agent(BaseModel):
    agent_id: str
    agent_card: AgentCard      # from a2a.types — external, not extensible
    # ... existing fields ...

    # Hub-sourced agent fields (new)
    source: str = "cloud"          # "cloud" | "hub"
    hub_id: str | None = None
    hub_owner_id: str | None = None
    is_hub_online: bool = False
    local_agent_id: str | None = None  # hub-assigned id for dedup
```

> **Note on `local_agent_id`**: Each hub assigns a `local_agent_id` to its
> local agents. The cloud backend uses `(hub_id, local_agent_id)` as a
> composite dedup key for upserts — ensuring re-syncs update the same
> `Agent` document rather than creating duplicates. The cloud-minted
> `agent_id` is the sole stable identifier; it is set via `$setOnInsert`
> on first sync and never overwritten by subsequent syncs.

> **Note on AgentCard**: The `agent_card` field uses `AgentCard` from the external `a2a` Python package (`a2a.types`). This type cannot be extended with custom fields like `identity`. Hub-specific and trust-layer metadata must live as sibling fields on the `Agent` model, not nested inside `agent_card`.

> **Note on `normalized_url` and the "one document" strategy**: When the hub
> syncs an agent, the relay service computes `normalized_url` from the
> agent card's URL using the same `normalize_agent_url()` function used by
> web UI registration. It then looks up an existing agent by
> `(normalized_url, provider_id)`. If an agent with that URL already exists
> for the same user (e.g. registered earlier via the web UI), the sync
> **enriches** the existing document with hub metadata (`hub_id`,
> `hub_owner_id`, `local_agent_id`, `is_hub_online`) but **preserves the
> original `source` field**. This means:
>
> - A cloud-registered agent that is also discovered by the hub remains
>   `source: "cloud"` and continues to be directly callable via the gateway.
>   It simply gains additional hub metadata so the platform knows the hub
>   can also reach it.
> - A truly hub-only agent (no prior web UI registration) is created with
>   `source: "hub"` and its proper `normalized_url`.
> - The `provider_id` filter on the lookup prevents cross-user enrichment
>   (User B's hub cannot enrich User A's agent document).
>
> This "one document per agent URL per user" approach avoids the
> `DuplicateKeyError` that occurred when the sync tried to insert a second
> document with `normalized_url: null`, and ensures consistent routing
> regardless of whether the agent was registered via web UI, hub, or both.

> **Note on `normalized_url` unique index**: The `unique_normalized_url`
> MongoDB index uses a **partial filter expression**
> (`{normalized_url: {$type: "string"}}`) instead of `sparse: true`. This
> ensures uniqueness only among documents that have a non-null string
> `normalized_url`, allowing multiple documents with `normalized_url: null`
> (e.g. legacy agents without URLs). The index is automatically ensured at
> server startup via `mongodb.ensure_agent_indexes()`.

> **Note on `AgentCard.url`** `[v1.0-MIGRATION]`: For hub agents, `agent_card.url` should be set to the gateway proxy URL (`https://api.hybro.ai/v1/gateway/agents/{agent_id}/message/send`). This ensures that any code path reading `agent_card.url` gets a routable address. The actual routing (relay vs. direct) is determined by `agent.source`, not by the URL. In A2A v1.0, `AgentCard.url` is removed in favor of `supportedInterfaces[0].url`; the `A2AClient` constructor is expected to resolve this internally, so code should use the SDK's higher-level client APIs rather than reading `agent_card.url` directly.

The existing `GET /agent/getAllActiveAgents` endpoint already returns all
active agents. Hub agents automatically appear once they're in MongoDB —
**no changes needed** to this endpoint or the frontend's `allAgentsQuery`.

### 5.2 Connection Model

The hub initiates all connections. The cloud never reaches in.

```
Hub (outbound) ──SSE subscribe──→ hybro.ai /api/v1/relay/hub/{hub_id}/events
Hub (outbound) ──HTTP POST──────→ hybro.ai /api/v1/relay/hub/{hub_id}/publish
```

### 5.3 Message Relay Flow

1. User opens hybro.ai, sends a message in a room that includes hub agents.
2. Backend's `sendMessage` creates the user message and queues
   `room_message_center.process_room_user_message` as a background task
   (unchanged from current flow).
3. The orchestration layer (SupervisorExecutor or QueueExecutor) decides
   which agents to call. When dispatching to a specific agent via
   `AgentMessageProcessor.process_single_message`, a `DispatchChain`
   of middleware runs before the actual dispatch:
   - `HubTransportMiddleware` inspects `agent.source`:
     - **`"cloud"`**: Transport stays `"direct"` (existing A2A behavior).
     - **`"hub"`**: Transport is set to `"relay"`. If the hub is offline,
       `ctx.metadata["queued_for_offline"]` is also set.
   - If transport is `"relay"`, the processor pushes a `RelayToHubEvent`
     to the hub's SSE queue (or offline queue) and returns
     `ProcessingStatus.RELAY_DISPATCHED`. Both `SupervisorExecutor` and
     `QueueExecutor` treat `RELAY_DISPATCHED` like `PAUSED` — they persist
     continuation state and wait for the hub to publish a response.

   > **Key design point**: Routing is per-agent, not per-message. A single
   > user message in a room with both cloud and hub agents triggers both
   > paths. The routing split happens inside `AgentMessageProcessor` via
   > the `DispatchChain`, not at the `sendMessage` HTTP handler.

   > **Lazy initialization**: `AgentMessageProcessor` lazily resolves the
   > `relay_service` singleton on first use (via `_ensure_relay_initialized`).
   > This is necessary because the `RoomMessageCenter` module-level singleton
   > is instantiated at import time, before `init_relay_service()` runs
   > during the FastAPI lifespan.

4. Hub receives the event, dispatches to the local agent via A2A.
5. Hub publishes results via `POST /api/v1/relay/hub/{hub_id}/publish`:
   ```json
   {
     "room_id": "...",
     "events": [
       {"type": "task_submitted", "data": {...}},
       {"type": "agent_token", "data": {"token": "Hello", "message_id": "..."}},
       {"type": "agent_response", "data": {"message_id": "...", "content": "..."}},
       {"type": "processing_status", "data": {"status": "completed"}}
     ]
   }
   ```
6. Relay service receives events, persists as `RoomAgentMessage` documents
   in MongoDB, and broadcasts via the existing `SSEManager.broadcast_to_room()`.
7. Frontend receives events via its normal SSE connection — identical to
   cloud-orchestrated responses.

**Event-to-DB translation:** The relay translates hub events to DB records:

| Hub Event | DB Action |
|-----------|-----------|
| `task_submitted` | Create `RoomAgentMessage` with `task_state="submitted"` |
| `agent_token` | Stream via `SSEManager.send_agent_token()` (no DB write) |
| `agent_response` | Update `RoomAgentMessage.message_content`, set `task_state="completed"` |
| `processing_status` | Update `Room.processing_message_id` |

### 5.4 Hub ID Lifecycle

**First-time setup:**
1. User runs `hybro-hub start --api-key hba_...`
2. Hub generates a local UUID as `hub_id`
3. Hub calls `POST /api/v1/relay/hub/register` with the API key
4. Cloud validates the API key, stores `hub_id → user_id` mapping
5. Hub persists `hub_id` in `~/.hybro/hub_id`

One user can have multiple hubs. A room targets a specific hub's agent
(identified by `agent_id` which encodes the `hub_id`).

### 5.5 Authentication Bridge

The relay bridges two auth systems: frontend uses Clerk JWT, hub uses API key.

- Hub connects with API key → relay resolves `user_id` from `APIKey` model
- Frontend calls `sendMessage` with Clerk JWT → backend resolves `user_id`
- Relay verifies: `api_key.user_id == room.room_owner_id` before forwarding

**Relay Publish Security**: All relay endpoints use API key authentication,
including `POST .../publish`. Publish requests are further validated by:

- **Hub ownership**: The hub's `user_id` (resolved from API key) must match
  the hub document in the database.
- **Room ownership**: The hub owner must match the room owner.
- **Message ownership**: Each `agent_message_id` is validated to belong to
  the publishing hub (checked in `RelayTransport`).

**SSE Connection Resilience**: The hub's SSE client uses a 90-second read
timeout (3x the server heartbeat interval). If no SSE event arrives within
this window, the connection is treated as dead and automatically reconnected
with exponential backoff. Publish is decoupled from SSE health — agent
responses are delivered even during SSE reconnection.

- **Heartbeat-based liveness**: The relay sends periodic heartbeat events. If
  the hub fails to send heartbeats for 3 consecutive intervals, the server
  closes the connection. On reconnect, the API key is re-validated.

### 5.6 Offline Handling

- If the hub's SSE drops, relay marks all hub agents as `is_hub_online = false`
- This includes both hub-only agents (`source: "hub"`) and enriched cloud
  agents that have `hub_id` set. Enriched cloud agents remain callable via
  the gateway (their `source` is still `"cloud"`); only the `is_hub_online`
  flag changes.
- Frontend's next `getAllActiveAgents` fetch shows hub-only agents grayed out
- User messages to offline hub agents are queued with `pending_for_hub` flag
- When hub reconnects, queued messages are delivered **in send order** (FIFO)
- UI shows: "Hub offline — messages will be delivered when your hub reconnects"
- **No fallback to cloud orchestration** — respects user's privacy choice

**Queue parameters:**

| Parameter | Default | Rationale |
|-----------|---------|-----------|
| Max queue depth per hub | 100 messages | Prevents unbounded growth if hub is offline for days |
| Message TTL | 24 hours | Messages older than TTL are discarded with a notification to the user |
| Overflow behavior | Reject with 503 + user notification | UI shows "Hub offline too long — please restart your hub" |
| Delivery order | Strict FIFO | Messages delivered in the order the user sent them |

Messages that expire or overflow are marked as failed in the database
(the `RoomAgentMessage.message_content.message_task.status` is set to
`TaskState.failed`) and the user receives an SSE notification: "N messages
to your local agents expired while your hub was offline." The heartbeat loop
periodically sweeps expired entries from in-memory offline queues to prevent
memory leaks.

> **Concurrency note**: **Authoritative busy state** for orchestration is
> **`runs` / `active_runs`** (not `rooms.processing_message_id`, which is legacy
> and may be nulled by cleanup when no non-terminal runs exist). In rooms with
> both cloud and hub agents, a supervisor dispatch may call a fast cloud agent
> and a slow hub agent concurrently. The relay must not emit **terminal**
> lifecycle for that user turn until every in-flight agent leg has finished.
>
> **Mechanism**: The backend tracks in-flight agent dispatches per user message
> using an atomic counter or a set of pending `agent_message_id`s on the
> `RoomUserMessage` document (e.g., `pending_agent_ids: set[str]`). Each
> agent completion (cloud direct or hub relay) removes its ID from the set.
> Terminal `processing_status` / run transitions apply only when the set is
> empty—analogous to `asyncio.gather`, but persisted to handle relay-delayed
> completions. Per-agent `task_update` SSE events provide progress while
> agents are in-flight.

### 5.7 Webhook Relay for Push Notifications

When the hub calls a cloud agent via the gateway, the agent may send push
notification webhooks. These arrive at the gateway (hub has no public URL).
The gateway's existing webhook handler (`api/webhooks.py`) checks if the
task owner has a connected hub and forwards the update to the hub's relay
SSE queue.

---

## 6. Privacy & Data Routing

### 6.1 The Privacy Router

The privacy router classifies each user request by sensitivity and decides
whether it can be sent to cloud agents.

### 6.2 Sensitivity Classification

| Level | Meaning | Routing |
|-------|---------|---------|
| **HIGH** | Personal data, medical/legal/financial info, credentials, proprietary code | Local agents only |
| **MEDIUM** | Business context, internal discussions | Local preferred, cloud with anonymization |
| **LOW** | General knowledge, public info, translations | Any agent |

**Classification methods (progressive):**

1. **Keyword matching** (Phase 1): User-configured sensitive keywords in
   `config.yaml`. Simple, transparent.
2. **Pattern detection** (Phase 1): Regex for PII (email, phone, SSN, credit
   card, API keys). Redacted before cloud transmission.
3. **LLM classification** (Phase 3): Local LLM classifies sensitivity.
4. **User override** (all phases): User marks a message as "local only" or
   "cloud OK" via a UI toggle.

### 6.3 Data Minimization

When a request goes to a cloud agent, the hub applies:
- **Context windowing:** Only send relevant messages, not full history.
- **Placeholder substitution:** "John Smith" → "[PERSON_1]", re-substituted
  in the response.
- **Task extraction:** Formulate a sanitized task instead of raw user input.

### 6.4 Privacy Indicators in the UI

Badges next to each agent response in the chat:
- 🏠 **Local** (green shield): "Processed locally — data did not leave your machine"
- ☁️ **Cloud** (blue cloud): "Processed by [Agent Name] via hybro.ai cloud"
- 🔀 **Mixed** (split icon): "Orchestrated locally, [Agent Name] called via cloud"

---

## 7. Portal-First Web UI

### 7.1 Design Goal

The user always uses `hybro.ai`. No mode switching, no `localhost` URL, no
separate local UI. Hub agents appear alongside cloud agents in the same portal.

### 7.2 Frontend Changes (Minimal)  ✅ IMPLEMENTED

Since the portal-first approach keeps the user on hybro.ai at all times, the
frontend changes are small compared to the v2 dual-mode design:

1. **Agent source badge**: In agent cards and selector chips, a Lucide icon
   (`House` for hub, `Cloud` for cloud) with a tooltip indicating source and
   online status. Implemented as `AgentSourceBadge` — a standalone component
   rendering just the icon wrapped in a `Tooltip`, accepting a `className`
   for flexible sizing across contexts.

   > **Frontend type change**: The `Agent` interface in both
   > `src/lib/types/response.ts` and `src/lib/types/agent.ts` now includes
   > `source?: "cloud" | "hub"`, `hub_id?: string`, `hub_owner_id?: string`,
   > `is_hub_online?: boolean`, and `local_agent_id?: string`.

2. **Hub status indicator**: The "My Hub" settings section displays hub
   connection status (online/offline/no hub) with last-connected timestamp.
   Data comes from a new Clerk-authenticated `GET /api/v1/hub/my-status`
   endpoint (see Phase 2c deliverable 8 and Phase 2a relay API deliverable 3).

3. **Hub setup page**: A new `HubSection` component in the settings dialog
   with three states: (a) No hub — setup instructions with link to API keys,
   (b) Hub online — green status, connected agents list, (c) Hub offline —
   amber status, dimmed agent list. Uses TanStack React Query with 30s
   `staleTime` for the hub status endpoint.

4. **Offline agent styling**: When `agent.is_hub_online === false` and
   `agent.source === "hub"`, agent cards and selector chips are dimmed
   (`opacity-50`). The status dot changes to a gray pulse. Tooltip reads:
   "Hub offline — start your hub to use this agent."

5. **Privacy badge on messages**: A small inline pill in the
   `AgentMessageBubbleInner` header showing `Shield` + "Local" (green) for
   hub agent messages or `Cloud` + "Cloud" (blue) for cloud agent messages.
   The `agentSource` field is carried through the Zustand message store
   (`MessageEntity.agentSource`) and populated from the agent's `source`
   field during SSE event processing and DB message loading.

6. **Agent ordering**: In the agent selector, hub agents are sorted after
   cloud agents in the unselected list, relying on the source badge icon
   for visual distinction rather than section headers.

### 7.3 What's NOT Needed (vs v2 Dual-Mode Design)

| v2 Requirement | Portal-First | Why |
|---------------|-------------|-----|
| Local API server on hub | Not needed | Frontend talks to cloud, not hub |
| Hub SSE server | Not needed | Browser SSE connects to cloud as today |
| Hub SQLite for rooms/messages | Not needed | All stored in cloud MongoDB |
| Clerk auth bypass | Not needed | Always Clerk auth via hybro.ai |
| Cloud-only page gating | Not needed | All pages work, hub agents are just more agents |
| Frontend mode switcher | Not needed | One mode: cloud |
| Mixed-content handling | Not needed | No localhost connections from browser |
| Bundled Next.js static files in hub | Not needed | No local UI to serve |
| API compatibility layer (20+ endpoints) | Not needed | Hub doesn't serve REST APIs |

---

## 8. Protocol & Compatibility

### 8.1 A2A as the Universal Agent Protocol

All agent communication uses A2A (v0.3). Local-to-local, local-to-cloud, cloud-to-cloud.

| Capability | Usage |
|------------|-------|
| `message/send` `[v1.0-MIGRATION: renamed to SendMessage]` | Synchronous dispatch to local and cloud agents |
| `message/stream` `[v1.0-MIGRATION: merged into SendMessage with streaming param]` | Streaming dispatch (real-time token output) |
| `tasks/get` | Poll long-running tasks on cloud agents |
| `tasks/cancel` | Cancel running tasks |
| Agent Card discovery | Auto-discover local agents via `/.well-known/agent-card.json` |
| Push Notifications | Cloud agents push task updates via gateway webhook relay (§5.7) |

### 8.2 MCP for Local Tools

The hub supports MCP servers for tool integration (file access, database,
APIs). Complementary to A2A:
- **A2A** = agent-to-agent (opaque, conversational)
- **MCP** = agent-to-tool (structured, function-call style)

### 8.3 AG-UI for UI Streaming (Future)

AG-UI could replace the custom SSE event format for hub-to-frontend streaming.
Standardized event types (`TEXT_MESSAGE_CONTENT`, `TOOL_CALL_START`, etc.).
Future optimization, not Phase 1.

---

## 9. A2A Version Strategy

This design targets **A2A v0.3** (the current `a2a-sdk` release). A2A v1.0 is
expected within 1-2 months and introduces breaking changes at the wire and SDK
level. The implementation should stay on v0.3 now and prepare a clean migration
path.

### 9.1 Approach

- **Pin SDK**: `a2a-sdk >=0.3, <1.0` in dependency files.
- **Use higher-level SDK APIs**: Stop manually constructing `SendMessageRequest`
  with hardcoded `method="message/send"` strings. Use
  `a2a_client.send_message(MessageSendParams(...))` instead so the SDK owns
  method names and request wrapping.
- **Don't build speculative abstraction layers** against a v1.0 API that
  doesn't exist yet. Add `# TODO(a2a-v1.0)` markers at migration-sensitive
  call sites instead.
- **Extend existing helpers**: Migration-sensitive logic goes in the existing
  `services/a2a_constants.py` and `common/utils/a2a_helpers.py`, not a new
  compat module.

### 9.2 Known Migration Points

| v0.3 → v1.0 Change | Affected Code | Migration Action |
|---------------------|---------------|------------------|
| `agent_card.url` removed → `supportedInterfaces[0].url` | `a2a_service.py` (~6 sites) | Refactor to use `A2AClient` factory which resolves the URL internally |
| `TextPart`/`FilePart`/`DataPart` → unified `Part` | ~30 call sites across 6 files | Wait for SDK; existing `a2a_helpers.py` abstracts part extraction via `getattr(part, "root", part)` |
| `result.kind` discriminator → member-based | `a2a_service.py`, `ResponseProcessor.py` | Centralize in `a2a_helpers.py` functions |
| `"message/send"` → `"SendMessage"` method name | `a2a_service.py` (6 hardcoded strings) | Eliminate by using higher-level SDK client API |
| Enum values `"completed"` → `"TASK_STATE_COMPLETED"` | DB `last_notified_state` field | Likely SDK-managed; verify on v1.0 release |
| Stream event `kind` → wrapper-based events | `ResponseProcessor.py` | Update match statement in streaming handler |
| `AgentCard` JWS signatures | Trust Layer Phase 0 | Adopt native verification instead of custom challenge-response |
| `extensions[]` on `Message`/`Artifact` | Trust Layer per-message tagging | Use for trust metadata instead of custom fields |
| Native multi-tenancy (`tenant` field) | Enterprise features (Phase 3) | Adopt when Organization model is built |

### 9.3 What v1.0 Gives Us for Free

Several features designed in the Trust Layer (HYBRO_TRUST_LAYER_DESIGN.md)
become simpler with A2A v1.0:

- **AgentCard JWS signatures** (JCS canonical form) provide native
  cryptographic identity verification, reducing the need for custom
  challenge-response in Trust Layer Phase 0.
- **`extensions[]` on Message and Artifact objects** enables per-message
  trust tagging (scopes, trace context) without custom fields.
- **`blocking` parameter on SendMessage** lets the hub explicitly request
  sync vs. async dispatch, simplifying the relay flow.

---

## 10. User Journey

### 10.1 Discovery

User is an existing hybro.ai user. They see a banner: "Run agents on your own
machine. Keep your data private." Or they find a "My Hub" tab in settings.

### 10.2 Setup (~5 minutes)

1. **Install**: `pip install hybro-hub` (or `brew install`, or download binary)
2. **API key**: Click "Generate API Key" on hybro.ai settings page. Copy it.
3. **Start**: `hybro-hub start --api-key hba_abc123...`
4. **Agent**: `hybro-hub agent start ollama --model llama3.2:8b` (bundles an
   A2A wrapper around Ollama)

Terminal output:
```
🔗 Connected to hybro.ai as "My MacBook Pro"
📡 Found 1 local agent:
   • My Ollama Chat (llama3.2:8b) — localhost:8001
Agents synced to hybro.ai. Open hybro.ai to start chatting.
```

### 10.3 First Chat

User refreshes hybro.ai. In "Add agents to room" they see:

```
Cloud Agents:
  ☁️  Legal Contract Reviewer
  ☁️  Code Review Pro

Your Local Agents:
  🏠  My Ollama Chat (llama3.2:8b)
      via My MacBook Pro • Online
```

They add the local agent, type a message. It routes through the relay to the
hub, which dispatches to the local Ollama agent. Streaming tokens flow back
through the relay to the browser. The response shows a 🏠 badge.

### 10.4 Daily Use

- User opens `hybro.ai` on any device. Hub is running in background.
- Local and cloud agents are mixed in rooms. Routing is transparent.
- From phone: local agents work if hub is online. Otherwise grayed out.
- Cloud agents always work regardless of hub status.

### 10.5 Error Scenarios

| Scenario | What User Sees |
|----------|---------------|
| Hub crashes mid-conversation | "⚠️ Your hub disconnected. Response may be incomplete." Cloud agents still work. |
| User sends to offline hub agent | "📨 Message queued — will be delivered when your hub comes online" |
| Slow local agent (30s+ response) | Normal processing spinner with "🏠 Processing on My MacBook Pro..." |
| Hub API key revoked | Hub agents disappear from agent list. Hub logs auth error. |

---

## 11. Phased Implementation Roadmap

### Phase 1: Gateway API + SDK (4–6 weeks)  ✅ IMPLEMENTED

**Goal:** Enable local agents to discover and call cloud agents via hybro.ai.

**Status:** Complete. All deliverables implemented and tested.

**Deliverables:**
1. Gateway API endpoints in `multi-agents-backend` (discover, send, stream, card)
   - Router: `api/gateway.py` — 4 endpoints with X-API-Key auth
   - Service: `services/gateway_service.py` — agent lookup, access control, URL masking, usage tracking
   - Rate limiting: `services/gateway_rate_limit_service.py` — per-key and global limits
   - DB migration: `database/migration/add_gateway_api_requests_indexes.py` — TTL index
   - Config: `gateway_base_url`, `gateway_rate_limit_per_key`, `gateway_rate_limit_global` in `config/settings.py`
   - CORS: Extended `DiscoveryCORSMiddleware` to cover `/gateway` paths
2. API key auth with `user_id` mapping (reuses existing `common/api_key_auth.py`)
3. Python SDK (`hybro-sdk` in `hybro-hub` repo):
   ```python
   from hybro_sdk import HybroGateway
   async with HybroGateway(api_key="hba_...") as gw:
       agents = await gw.discover("legal contract review")
       async for event in gw.stream(agents[0].agent_id, "Review..."):
           print(event.data)
   ```
   - Client: `hybro_sdk/client.py` — `discover()`, `send()`, `stream()`, `get_card()`
   - SSE parser: `hybro_sdk/_sse.py` — typed error mapping for streaming
   - Error hierarchy: `hybro_sdk/errors.py` — `AuthError`, `AccessDeniedError`, `AgentNotFoundError`, `RateLimitError`, `AgentCommunicationError`
   - Shared `raise_for_status()` mapper used by both sync and streaming paths
4. Documentation:
   - API guide: `docs/GATEWAY_API.md`
   - SDK README: `hybro-hub/README.md`
5. Tests:
   - Backend: `tests/test_api_gateway.py` (15 tests — endpoints, access control, URL masking, streaming)
   - SDK: `hybro-hub/tests/test_client.py` + `test_sse.py` (13 tests)

**Key implementation decisions:**
- **Stream endpoint auth** is validated eagerly via `prepare_stream()` (regular async method) before starting the SSE response. This ensures 401/403/404/429 errors return proper HTTP status codes, not SSE error events.
- **Discovery agent_id resolution** uses batch `$in` queries (2 queries max) instead of per-result lookups, avoiding N+1 performance issues.
- **Usage tracking** records both success and failure for all call types (sync and streaming) via `mongodb.increment_agent_call_count()`.
- **Response URL rewriting** is limited to discovery results and the card endpoint for Phase 1. Deep inspection of A2A response bodies (§4.2) is deferred to Phase 2.

**Validation:** 10+ developers use the SDK to call cloud agents from local code.

### Phase 2: Hub + Relay MVP (6–8 weeks)  ✅ IMPLEMENTED

**Goal:** Users install a hub, their local agents appear on hybro.ai.

**Status:** Complete. All sub-phases (2a, 2b, 2c) implemented and tested.

#### Phase 2a: Cloud Relay Service + Dispatch Middleware (backend)  ✅ IMPLEMENTED

**Status:** Complete. All deliverables implemented and tested.

**Deliverables:**
1. **Relay Service** (`services/relay_service.py`)
   - Hub registration and ownership validation
   - SSE connection pool (in-memory `asyncio.Queue` per `hub_id`)
   - Event routing: push `RelayToHubEvent` events to hub queues
   - Publish processing: receive hub events, verify connection token + room ownership,
     update `RoomAgentMessage` documents, broadcast via `SSEManager`, resume
     `SupervisorExecutor` / `QueueExecutor` orchestration
   - Heartbeat loop with configurable miss limit
   - Offline queue per hub (max depth, TTL, periodic sweep of expired entries)
   - Connection-scoped JWT tokens for `/publish` authentication

2. **Relay API** (`api/relay.py`) — 5 endpoints:
   - `POST /api/v1/relay/hub/register` — hub registration
   - `GET /api/v1/relay/hub/{hub_id}/events` — SSE event stream to hub
   - `POST /api/v1/relay/hub/{hub_id}/publish` — events from hub to cloud
   - `POST /api/v1/relay/hub/{hub_id}/agents/sync` — agent synchronization
   - `GET /api/v1/relay/hub/status` — hub status for API-key-authenticated hub

3. **Hub Status API** (`api/hub.py`) — 1 endpoint (added in Phase 2c):
   - `GET /api/v1/hub/my-status` — Clerk-authenticated endpoint for the
     frontend to fetch hub connection status. Returns `HubStatusResponse`
     with a list of hubs, each including `hub_id`, `is_online`,
     `last_connected_at`, and `agent_count`. This is separate from the
     relay's `GET /api/v1/relay/hub/status` (which uses API key auth for
     hub daemons) because the frontend authenticates via Clerk JWT, not
     API keys.

3. **Data Models** (`models/hub.py`):
   - `Hub`, `HubStatus`, `HubStatusResponse` (registration & status)
   - `RelayToHubEvent` (cloud → hub events)
   - `HubPublishEvent`, `HubPublishRequest` (hub → cloud events)
   - `HubAgentSync`, `HubAgentSyncRequest`, `HubAgentSyncResponse` (agent sync)

4. **Agent Model Extensions** (`models/agent.py`):
   - `source: str = "cloud"` — `"cloud"` or `"hub"`
   - `hub_id: str | None` — owning hub
   - `hub_owner_id: str | None` — user who registered the hub
   - `is_hub_online: bool = False` — live hub connection status
   - `local_agent_id: str | None` — hub-assigned ID for dedup

5. **Processing Status Extension** (`models/processing.py`):
   - `RELAY_DISPATCHED = "relay_dispatched"` — new status for relay-routed messages

6. **Dispatch Middleware Architecture** (`modules/dispatch_middleware.py`):
   - `DispatchContext` dataclass — carries dispatch state through middleware chain
   - `DispatchMiddleware` protocol — `pre_dispatch` / `post_dispatch` hooks
   - `DispatchChain` — executes middleware in order (pre) / reverse order (post)

7. **Hub Transport Middleware** (`modules/middleware/hub_transport.py`):
   - `HubTransportMiddleware` — inspects `agent.source`, sets `ctx.transport = "relay"`,
     flags `queued_for_offline` if hub is offline, guards against missing `hub_id`

8. **AgentMessageProcessor Integration** (`modules/AgentMessageProcessor.py`):
   - Lazy relay service resolution via `_ensure_relay_initialized()`
   - Pre-dispatch middleware chain execution
   - `_dispatch_via_relay()` — pushes `RelayToHubEvent` to relay service
   - `RELAY_DISPATCHED` result handling

9. **Orchestrator Integration**:
   - `SupervisorExecutor.py` — maps `RELAY_DISPATCHED` → `StepStatus.PAUSED`
   - `QueueExecutor.py` — maps `RELAY_DISPATCHED` → saves continuation state
   - `RoomMessageCenter.py` — removed eager relay wiring (lazy via AMP)

10. **Database Extensions** (`database/mongodb.py`):
    - `hubs_collection` property
    - `upsert_hub()`, `get_hub()`, `get_hubs_by_user()`, `update_hub_status()`
    - `upsert_hub_agent()` — uses `$setOnInsert` for `agent_id` stability
    - `set_hub_agents_online_status()` — filters by `hub_id` (not `source`)
      so enriched cloud agents also get their `is_hub_online` toggled
    - `count_hub_agents()` — filters by `hub_id` (not `source`) so enriched
      agents are included in hub agent counts
    - `ensure_agent_indexes()` — called at startup, ensures
      `unique_normalized_url` uses a partial filter expression instead of
      sparse (allowing multiple `null` values)
    - Migration: `database/migration/add_hub_indexes.py`
    - Migration: `database/migration/deduplicate_agents.py` — deduplicates
      agents by normalized URL and creates the partial unique index

11. **Supporting Changes**:
    - `config/settings.py` — `relay_heartbeat_interval`, `relay_offline_queue_max`,
      `relay_offline_queue_ttl`,
      `relay_hub_agent_heartbeat_miss_limit`
    - `common/middleware/discovery_cors_middleware.py` — extended to `/relay` paths
    - `jobs/stale_task_checker.py` — skips `source == "hub"` agents in orphan recovery
    - `services/gateway_service.py` — rejects hub agents with 502 (prevents
      self-referential URL loops)
    - `main.py` — relay router, lifespan init/shutdown

12. **Tests**:
    - `tests/test_api_relay.py` — 11 tests (registration, sync, SSE, publish with auth, offline queue)
    - `tests/test_dispatch_middleware.py` — 10 tests (chain ordering, hub transport, AMP relay dispatch)

**Key implementation decisions:**
- **"One document" agent sync strategy**: When the hub syncs an agent whose
  URL already exists in the DB for the same user, the relay service enriches
  the existing document with hub metadata instead of creating a second
  document. The `source` field is preserved (a cloud-registered agent stays
  `source: "cloud"`), ensuring gateway routing continues to work. Only truly
  new hub-only agents get `source: "hub"`. This avoids `DuplicateKeyError`
  on the `unique_normalized_url` index and ensures a single canonical document
  per agent URL per user.
- **`$setOnInsert` for `agent_id`**: The `upsert_hub_agent()` method uses
  `$setOnInsert` for `agent_id` so re-syncs never overwrite an existing agent's
  identity. The gateway proxy URL is written in a separate `update_one` call
  using the stable `stored_id`.
- **Lazy relay service resolution**: `AgentMessageProcessor` resolves the relay
  service singleton on first call, not at construction time. This sidesteps the
  DI timing issue where `RoomMessageCenter` is instantiated at module import
  before `init_relay_service()` runs.
- **`RELAY_DISPATCHED` reuses PAUSED semantics**: Both `SupervisorExecutor` and
  `QueueExecutor` treat `RELAY_DISPATCHED` identically to `PAUSED`, persisting
  continuation state for later resume when the hub publishes a response.
- **Heartbeat miss counter**: The miss counter is reset both when a real event
  is delivered AND when a heartbeat is sent (proving the SSE connection is alive),
  preventing false disconnections of idle-but-healthy hubs.
- **Offline queue sweep**: The heartbeat loop invokes `sweep_offline_queues()`
  periodically to evict expired entries and prevent memory leaks.
- **Failed message marking**: When offline messages expire or overflow, the
  `RoomAgentMessage` is updated with error text and its task status set to
  `TaskState.failed` (not just a no-op SSE notification).
- **Gateway hub agent guard**: `GatewayService.send_message()` and
  `prepare_stream()` reject hub-sourced agents with HTTP 502 (prevents
  self-referential URL loops). Combined with `HubTransportMiddleware`
  routing hub agents to the relay in the platform path, the self-referential
  `agent_card.url` is never followed for hub agents in any active code path.
  ✅ Verified in Phase 2c review.

#### Phase 2b: Hub Daemon + Ollama Adapter  ✅ IMPLEMENTED

**Status:** Complete. All deliverables implemented and tested.

**Goal:** Ship the `hybro-hub` PyPI package that connects to the relay,
and an Ollama A2A adapter in the `a2a-adapter` library.

**Deliverables:**

1. **Relay Client** (`hub/relay_client.py`):
   - SSE subscription to `GET /api/v1/relay/hub/{hub_id}/events` with auto-reconnect
     and exponential backoff (max 60s)
   - Separate `httpx.AsyncClient` instances with distinct timeout configs:
     - HTTP client: `connect=10, read=30, write=10` for register/sync/publish/status
     - SSE client: `connect=10, read=None, write=10` for long-lived event stream
       (prevents heartbeat-interval read timeouts from killing the connection)
   - `publish()` sends events to the backend with API key authentication
   - Publish is decoupled from SSE health — works even during SSE reconnection
   - `_flush_retry_queue()` drains queue into a local list before iterating,
     stops immediately if token is lost mid-flush, re-queues remaining items
   - Internal handling of `connection_token` and `heartbeat` SSE events
   - Network errors during publish are caught and queued for retry

2. **Hub Daemon** (`hub/main.py`):
   - `HubDaemon` orchestrator: config loading → relay registration → agent
     discovery → agent sync → SSE event loop
   - Per-event error isolation: unhandled exceptions in `_handle_event()` are
     caught and logged without crashing the daemon
   - Background tasks: periodic health checks (60s) and agent re-sync (120s)
   - Signal handling (SIGINT, SIGTERM) for graceful shutdown
   - Privacy classification on inbound messages (log-only in Phase 2b)

3. **Agent Registry** (`hub/agent_registry.py`):
   - Manual discovery from `config.yaml` agent list
   - Auto-discovery: enumerates all TCP ports listening on localhost via
     `psutil.net_connections()`, then probes each for A2A agent cards.
     This finds agents on any port (not just a fixed range). Well-known
     non-agent ports (22, 53, 80, 443, 3306, 5432, 6379, 27017) are
     excluded by default via `auto_discover_exclude_ports` config.
     Probes are concurrency-limited (30 at a time via `asyncio.Semaphore`).
   - Tries both `AGENT_CARD_WELL_KNOWN_PATH` (`/.well-known/agent-card.json`)
     and `PREV_AGENT_CARD_WELL_KNOWN_PATH` (`/.well-known/agent.json`) from
     `a2a.utils.constants` — supports both current and deprecated paths
   - URL-based dedup: re-discovery updates existing agent entries
   - `to_sync_payload()` for `HubAgentSyncRequest` format
   - Capability extraction from agent cards (streaming, push, skill tags)

4. **Dispatcher** (`hub/dispatcher.py`):
   - Sync dispatch via `message/send` (JSON-RPC 2.0 over HTTP POST)
   - Streaming dispatch via `message/stream` (JSON-RPC 2.0, SSE response)
   - JSON-RPC envelope unwrap in both sync (`_extract_text_from_response`) and
     streaming (`_extract_chunk_text`) response extraction
   - Translates A2A responses into `HubPublishEvent` format:
     `task_submitted` → `agent_token` (per chunk) → `agent_response` → `processing_status`
   - Graceful error handling: dispatch failures produce an error `agent_response`
     instead of crashing

5. **Privacy Router** (`hub/privacy_router.py`):
   - Keyword matching (user-configured, case-insensitive)
   - User-defined regex patterns
   - Built-in PII patterns: email, US phone, SSN, credit card, API key
   - `SensitivityLevel` enum: HIGH / MEDIUM / LOW
   - Phase 2b: `check_and_log()` classifies and logs but does NOT block dispatch

6. **Configuration** (`hub/config.py`):
   - `HubConfig` Pydantic model with all settings
   - Loads from `~/.hybro/config.yaml` with env var overrides (`HYBRO_API_KEY`,
     `HYBRO_GATEWAY_URL`) and CLI arg overrides
   - Hub ID persistence in `~/.hybro/hub_id` (generated once, reused)
   - `save_api_key()` for persisting API key to config file

7. **CLI** (`hub/cli.py` + `hub/__main__.py`):
   - `hybro-hub start [--api-key]` — starts the daemon (foreground)
   - `hybro-hub status` — queries relay for hub status
   - `hybro-hub agents` — discovers and lists local agents
   - `hybro-hub agent start ollama [--model] [--port] [--system-prompt]` — launches
     an Ollama A2A adapter via `a2a-adapter` library
   - Entry points: `hybro-hub` console script + `python -m hub`

8. **Ollama A2A Adapter** (`a2a-adapter` repo, `a2a_adapter/integrations/ollama.py`):
   - `OllamaAdapter` extending `BaseA2AAdapter` (v0.2 pattern)
   - Wraps Ollama `/api/chat` endpoint for both sync (`invoke`) and streaming
     (`stream`) — NDJSON streaming protocol
   - Configurable: model name, base_url, system_prompt, temperature, timeout,
     keep_alive
   - `get_metadata()` returns `AdapterMetadata` with `streaming=True`
   - Registered in loader (`_BUILTIN_MAP`), `__init__.py` lazy imports,
     `integrations/__init__.py`, and `pyproject.toml` optional deps
   - Example: `examples/ollama_agent.py` (3-line quickstart)

9. **Package Configuration** (`pyproject.toml`):
   - Dependencies: `httpx`, `httpx-sse`, `pydantic`, `pyyaml`, `click`, `a2a-sdk`, `psutil`
   - Optional: `ollama = ["a2a-adapter"]`
   - Console script: `hybro-hub = "hub.cli:main"`
   - Build includes both `hybro_sdk` and `hub` packages
   - `requires-python = ">=3.11"`

10. **Tests** (62 tests, all passing):
    - `test_relay_client.py` — 10 tests: register, sync, publish success/403/network-error,
      `_do_publish`, flush queue (success, 403-mid-flush, infinite-loop regression, no-token skip),
      timeout config, dead code cleanup verification
    - `test_agent_registry.py` — 9 tests: manual/auto discovery, fallback path,
      sync payload, health check, capability extraction
    - `test_dispatcher.py` — 12 tests: sync dispatch (success/error), JSON-RPC build,
      response extraction (status/artifacts/parts/root-wrapper), chunk extraction
      (raw/JSON-RPC-wrapped/root-wrapper/empty)
    - `test_privacy_router.py` — 11 tests: keyword/regex/PII detection, false positive check
    - `a2a-adapter/tests/unit/test_ollama_adapter.py` — comprehensive unit tests

**Key implementation decisions:**
- **Separate HTTP/SSE clients**: The relay client uses two `httpx.AsyncClient`
  instances with different timeout configs. The SSE client has `read=None` to
  prevent the backend's ~30s heartbeat interval from racing against a finite
  read timeout, which was causing constant reconnections.
- **Retry queue with circular-append prevention**: `publish()` queues events on
  failure (403, missing token, or network error). `_flush_retry_queue()` drains
  the queue into a local list and uses `_do_publish()` (which never touches the
  queue) to prevent infinite loops. If the token is lost mid-flush, remaining
  items are re-queued and the method returns immediately.
- **JSON-RPC envelope unwrap**: Both sync and streaming response extractors
  unwrap via `data.get("result", data)`, handling both JSON-RPC-wrapped and
  raw event payloads gracefully. This ensures streaming dispatch correctly
  extracts text from A2A SDK responses.
- **Per-event error isolation**: The daemon's event loop wraps `_handle_event()`
  in a try/except so a single failed event (publish error, dispatch error, etc.)
  is logged without crashing the daemon.
- **Ollama adapter in a2a-adapter repo**: The Ollama adapter follows the existing
  v0.2 `BaseA2AAdapter` pattern in the `a2a-adapter` library (not bundled in
  `hybro-hub`). The hub's CLI imports it at runtime via the `ollama` optional
  dependency. This keeps the adapter reusable outside the hub context.
- **Dual agent card path support**: Agent discovery and health checks try both
  `/.well-known/agent-card.json` (current) and `/.well-known/agent.json`
  (deprecated), importing the canonical paths from `a2a.utils.constants`.
  This ensures compatibility with agents using either the current or
  previous A2A SDK versions.
- **psutil-based port discovery**: Instead of scanning a fixed port range
  (which misses agents on non-standard ports and wastes requests on closed
  ports), auto-discovery uses `psutil.net_connections()` to enumerate all
  TCP ports actually listening on localhost, then probes only those. This
  is both faster and finds agents on any port. A configurable exclude list
  skips well-known non-agent ports (SSH, databases, etc.). Concurrency is
  bounded by `asyncio.Semaphore(30)` to avoid overwhelming the system.

#### Phase 2c: Frontend Updates  ✅ IMPLEMENTED

**Status:** Complete. All deliverables implemented and tested.

**Goal:** Show hub agents and status in the hybro.ai web portal.

**Deliverables:**

1. **Agent Type Extensions** (`hybro-frontend`):
   - `src/lib/types/response.ts` — added `source`, `hub_id`, `hub_owner_id`,
     `is_hub_online`, `local_agent_id` to the full `Agent` interface
   - `src/lib/types/agent.ts` — same fields on the simplified `Agent` interface

2. **Agent Source Badge** (`src/components/agent-source-badge.tsx`):
   - Reusable component rendering Lucide `House` (hub) or `Cloud` (cloud) icon
   - Wrapped in `Tooltip` with context-aware label (online/offline for hub)
   - Accepts `className` for flexible sizing in different contexts
   - Green icon for online hub, muted for offline hub, sky-blue for cloud

3. **Agent Card Integration** (`src/components/agent-card.tsx`):
   - `AgentSourceBadge` placed in top-right slot
   - Offline hub agents: `opacity-50` on card, gray pulsing status dot

4. **Agent Selector Integration** (`src/components/agent-selector.tsx`):
   - `AgentSourceBadge` next to agent name in both selected and unselected chips
   - Offline hub agents: `opacity-50`, "(offline)" suffix
   - Hub agents sorted after cloud agents in unselected list

5. **Privacy Badge on Messages** (`src/components/message-bubble.tsx`):
   - Inline pill in `AgentMessageBubbleInner` header after timestamp
   - Green `Shield` + "Local" for hub agents, blue `Cloud` + "Cloud" for cloud
   - Data flow: `agentSource` field added to `MessageEntity` and `IncomingMessage`
     in the Zustand message store (`src/stores/message-store/types.ts`,
     `src/stores/message-store/upsert.ts`)
   - Populated from agent's `source` field during SSE event processing
     (`src/hooks/useRoomWebhook.ts` — `getAgentSource` helper)
   - Also populated for DB-loaded messages via `convert-api-message.ts`

6. **Hub Settings Section** (`src/components/settings/hub-section.tsx`):
   - Three states: No Hub (setup instructions + link to API keys),
     Hub Online (green dot, last connected, agent list),
     Hub Offline (amber dot, last seen, dimmed agent list)
   - Uses `useQuery(['hub', 'status'], ...)` with 30s `staleTime`
   - Refresh button triggers cache invalidation
   - Integrated into `SettingsDialog` after the profile section

7. **Hub API Client** (`src/lib/api/hub.ts`):
   - `getMyHubStatus()` — calls `GET /api/v1/hub/my-status` with Clerk auth
   - TypeScript types: `HubStatus`, `HubStatusResponse`

8. **Backend: Hub Status Endpoint** (`multi-agents-backend/api/hub.py`):
   - `GET /api/v1/hub/my-status` — Clerk JWT authenticated
   - Returns `HubStatusResponse` via `relay_service.get_hub_status(user_id)`
   - Registered in `main.py` with `Depends(get_current_user)`
   - Separate from relay's `GET /api/v1/relay/hub/status` (API key auth)

**Files added/modified:**

| Repository | File | Role |
|------------|------|------|
| `hybro-frontend` | `src/lib/types/response.ts` | Hub fields on full Agent interface |
| `hybro-frontend` | `src/lib/types/agent.ts` | Hub fields on simplified Agent interface |
| `hybro-frontend` | `src/components/agent-source-badge.tsx` | **New** — House/Cloud icon badge component |
| `hybro-frontend` | `src/components/agent-card.tsx` | Source badge + offline styling |
| `hybro-frontend` | `src/components/agent-selector.tsx` | Source badge + offline styling + sort |
| `hybro-frontend` | `src/stores/message-store/types.ts` | `agentSource` on MessageEntity/IncomingMessage |
| `hybro-frontend` | `src/stores/message-store/upsert.ts` | Carry `agentSource` through upsert |
| `hybro-frontend` | `src/hooks/useRoomWebhook.ts` | `getAgentSource` helper, populate on SSE events |
| `hybro-frontend` | `src/stores/message-store/convert-api-message.ts` | Populate `agentSource` for DB-loaded messages |
| `hybro-frontend` | `src/components/message-bubble.tsx` | Privacy badge in message header |
| `hybro-frontend` | `src/components/settings/hub-section.tsx` | **New** — Hub settings section |
| `hybro-frontend` | `src/components/settings/settings-dialog.tsx` | Integrated HubSection |
| `hybro-frontend` | `src/lib/api/hub.ts` | **New** — Hub status API client |
| `multi-agents-backend` | `api/hub.py` | **New** — Clerk-auth hub status endpoint |
| `multi-agents-backend` | `main.py` | Mounted hub router |

**Key implementation decisions:**
- **Clerk-auth hub status endpoint**: The frontend uses Clerk JWT for auth,
  but the existing relay hub status endpoint uses API key auth (for hub
  daemons). A separate `GET /api/v1/hub/my-status` endpoint was added with
  `Depends(get_current_user)` to bridge this gap cleanly.
- **`agentSource` in message store (not React Query cache)**: The privacy
  badge needs agent source per message. Rather than reading from the React
  Query agent cache synchronously (a pattern not used elsewhere in the
  codebase), the `agentSource` is stamped onto each `IncomingMessage` when
  created — following the existing `senderName` pattern.
- **Icon-only badge (not shadcn Badge)**: The `AgentSourceBadge` renders just
  a Lucide icon wrapped in a Tooltip, not a full `Badge` component. This keeps
  it compact for tight UI spaces like agent chips and card slots.
- **Sort instead of section headers**: Hub agents are sorted after cloud agents
  in the agent selector rather than using "Cloud Agents" / "Local Agents"
  section headers, avoiding unnecessary layout changes.
- **Cloud badge visible for all agents**: Pre-hub cloud agents (without a
  `source` field) show a Cloud badge by design. This is intentional — the
  badge serves as a privacy indicator, and cloud is the correct default.

**Validation:** User runs `hybro-hub start`, opens hybro.ai, sees local agents
with 🏠 badges alongside cloud agents with ☁️ badges. Hub status visible in
settings. Messages show "Local" or "Cloud" privacy badges.

### Phase 3: Advanced Features (12+ weeks, parallel)

1. **Desktop app** — Tauri wrapper with system tray, auto-start
2. **Privacy router v2** — LLM-based classification, reversible anonymization
3. **History sync** — Opt-in encrypted sync to cloud (E2E encrypted)
4. **Enterprise** — SSO, audit trails, hub fleet management, VPC deployment

---

## 12. Competitive Landscape

### 12.1 Market Map

| Product | What They Do | Overlap with Hybro Hub | Key Difference |
|---------|-------------|----------------------|----------------|
| **Clarifai Local Runners** | "Ngrok for AI models." Local model connects outbound to cloud API. | Same relay pattern, same privacy benefit. | Models only, not agents. No orchestration, no marketplace. |
| **Pryx** | Sovereign AI agent control center. Rust binary, local-first, 22+ providers. | Same philosophy (local + cloud, privacy). | CLI/TUI tool, not a web portal. No agent marketplace. No relay. |
| **SmythOS Studio** | Visual agent builder with local/cloud/enterprise deployment. | Same "one product, any deployment" model. | Build tool, not a bridge. No mixing local + cloud in one conversation. |
| **AgentCenter** | Dashboard for OpenClaw agents across distributed infra. | Same "one portal, agents everywhere" concept. | Task management, not conversational chat. Locked to OpenClaw. |
| **Microsoft Foundry Local** | Hybrid AI: local LLM + Azure agents via MCP. | Same hybrid concept, uses A2A + MCP. | Developer SDK, not a web portal. Azure-locked. Cloud initiates connections to local. |
| **Dify / n8n / Langflow** | Self-hosted workflow builders supporting Ollama + cloud APIs. | Support local models alongside cloud. | No mixing in one conversation. Self-hosted = all local or all cloud. |

### 12.2 The Gap

No product combines: (1) a cloud web portal where local and cloud agents
appear side-by-side, (2) conversational real-time chat, (3) privacy-based
routing, (4) an agent marketplace, and (5) outbound-only relay. Clarifai
validates the architecture, Pryx validates the philosophy, AgentCenter
validates the dashboard concept. Hybro Hub assembles all pieces.

---

## 13. Risks & Mitigations

### 13.1 Technical Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Relay latency** (4+ network hops for local agent messages) | Noticeable delay vs direct cloud agents | Streaming passthrough (don't buffer). Show "via your hub" indicator so users understand. Most local agent value is privacy, not speed. |
| **Hub offline = local agents unavailable** | Frustrating when user forgets to start hub | Desktop app with auto-start (Phase 3). Clear "Hub offline" indicators. Cloud agents always work as fallback. |
| **Agent sync consistency** | Stale agent list if hub disconnects without cleanup | TTL on hub agents (mark offline after 60s heartbeat miss). Hub re-syncs on reconnect. |
| **Event format mismatch** between hub publish and backend SSE | Broken chat rendering | Strict event schema validation in relay. Integration tests covering full round-trip. |

### 13.2 Product Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Cold start problem** (hub does nothing without local agents) | Users install hub, see no value | Bundle Ollama A2A wrapper. `hybro-hub agent start ollama` gives instant local agent. |
| **Privacy theatre** (users assume privacy but data may leak) | Trust damage | Open-source hub. Privacy badges per message. Network traffic dashboard in hub CLI. |
| **Too complex for non-developers** | Small user base | Phase 1 targets developers. Desktop app (Phase 3) simplifies. One-click setup. |

### 13.3 Business Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Hub cannibalizes cloud revenue** | Users stop using cloud agents | Cloud agents offer capabilities local can't match. Hub drives discovery of cloud agents. |
| **Support burden** of local software | High support costs | Docker as escape hatch. Community forums. Phase 1 targets self-serve developers. |

---

## 14. Open Questions

### 14.1 [OPEN] Hybrid Orchestration

When a user's message could benefit from both local and cloud agents (e.g.,
"research X and apply to my private data"), who orchestrates? Options:
- Hub orchestrator splits the task (more complex hub)
- User explicitly picks an agent per message (simpler but manual)
- Cloud orchestrator with privacy-aware routing (requires cloud to understand
  which data is sensitive)

Phase 2 starts with explicit agent selection. Hybrid orchestration is Phase 3.

### 14.2 [OPEN] Room-Level vs Message-Level Agent Targeting

Current hybro.ai assigns agents to rooms (`room_agent_set`). With hub agents,
should the user:
- Assign agents to rooms as today (but now including local agents)?
- Pick per message which agent to use?
- Let the orchestrator decide automatically?

Phase 2: add local agents to `room_agent_set` same as cloud agents. The
frontend's "Add agents" flow works unchanged — hub agents just appear in
the available agents list.

### 14.3 [OPEN] Streaming Shortcut

The relay adds latency: local agent → hub → relay POST → backend SSE →
browser. For real-time token streaming, consider: hub sends tokens directly
to a WebSocket/SSE endpoint instead of batching via REST POST. This is an
optimization for Phase 3.

### 14.4 [OPEN] Air-Gapped Mode Scope

The air-gapped mode (§2.2 Mode 3) is listed as an escape hatch. How much
should we invest in it? Options:
- Minimal: hub serves a health/status endpoint on localhost. No chat UI.
- Moderate: hub serves a simple chat TUI in the terminal.
- Full: hub serves a local web UI (back to v2 complexity).

Recommendation: minimal for Phase 2. Revisit based on demand.

### 14.5 [OPEN] Multi-Hub Coordination

A user with multiple hubs (laptop + desktop). Both are online. A room has
agents from both hubs. When the user sends a message, which hub processes it?
The `agent_id` encodes which hub owns it, so routing is per-agent, not per-hub.
But cancellation, status, and ordering need careful design.

---

## 15. Cross-Document Integration Notes

This design must compose with two sibling documents:

- **[WORKFLOW_ENGINE_ROADMAP.md](./WORKFLOW_ENGINE_ROADMAP.md)**: The workflow engine dispatches agent tasks through `AgentMessageProcessor.process_single_message`. Hub routing is transparent to the workflow executor — the transport selection (relay vs. direct A2A) happens inside `AgentMessageProcessor` based on `agent.source`. No workflow-level changes are needed for hub support.

- **[HYBRO_TRUST_LAYER_DESIGN.md](./HYBRO_TRUST_LAYER_DESIGN.md)**: The trust layer adds pre-dispatch middleware (policy evaluation, token issuance) to `AgentMessageProcessor`. For hub agents, the token is issued by the cloud Token Service and forwarded through the relay to the hub. The hub caches policies locally for offline evaluation. Identity keys are stored in `~/.hybro/keys/`, making the hub not fully stateless (see §2.3 correction above).

### Unified Dispatch Path

When all three systems are active, the per-agent dispatch path is:

```
RoomMessageCenter.process_room_user_message
  → Is supervisor_v2? → SupervisorExecutor (decides which agents)
  → Is workflow trigger? → WorkflowExecutor (follows defined steps)
  → Each step dispatches via AgentMessageProcessor.process_single_message:
      1. Policy check (Cedar) — Trust Layer
      2. Token issuance (HCT) — Trust Layer
      3. Transport selection:
         - agent.source == "cloud" → direct A2A call
         - agent.source == "hub" → relay push — Hub Design
      4. Trace context injection — Trust Layer
      5. SSE progress events — all three designs
```

### Middleware Architecture for `AgentMessageProcessor`  ✅ IMPLEMENTED

The `DispatchMiddleware` pattern has been implemented in Phase 2a. The
`AgentMessageProcessor` now accepts a `DispatchChain` and runs pre/post
middleware hooks around the core dispatch logic.

**Implemented files:**
- `modules/dispatch_middleware.py` — `DispatchContext`, `DispatchMiddleware` protocol, `DispatchChain`
- `modules/middleware/hub_transport.py` — `HubTransportMiddleware`

```python
# modules/dispatch_middleware.py (implemented)

class DispatchMiddleware(Protocol):
    async def pre_dispatch(self, ctx: DispatchContext) -> DispatchContext: ...
    async def post_dispatch(self, ctx: DispatchContext, result: ProcessingResult) -> ProcessingResult: ...

class AgentMessageProcessor:
    def __init__(self, ..., dispatch_chain: DispatchChain | None = None):
        self.dispatch_chain = dispatch_chain or DispatchChain()

    async def process_single_message(self, ...) -> ProcessingResult:
        self._ensure_relay_initialized()  # lazy DI resolution
        ctx = DispatchContext(agent=agent, room_id=room_id, ...)

        # Pre-dispatch: transport selection, policy, etc.
        ctx = await self.dispatch_chain.run_pre_dispatch(ctx)
        if ctx.denied:
            return ProcessingResult(ProcessingStatus.FAILED, ctx.deny_reason)

        # Transport branch: relay or direct A2A
        if ctx.transport == "relay":
            return await self._dispatch_via_relay(ctx, current_message)

        # Core dispatch (existing direct A2A logic)
        result = await self._dispatch_direct(ctx)

        # Post-dispatch: logging, audit, etc.
        return await self.dispatch_chain.run_post_dispatch(ctx, result)
```

Current middleware:
- `HubTransportMiddleware` — sets `ctx.transport` to `"relay"` for hub agents,
  guards against missing `hub_id` (denies dispatch)

Future middleware (not yet implemented):
- `TrustPolicyMiddleware` — Cedar evaluation, token issuance (Trust Phase 1)
- `TraceContextMiddleware` — injects `traceparent`/`tracestate` headers
- `TransparencyMiddleware` — emits structured events and audit records

### SSE Event Composition

When a workflow step dispatches to a hub agent, the frontend may receive
*both* workflow-level events and hub relay events for the same operation:

- **Workflow events**: `workflow_progress` (step-level status)
- **Hub relay events**: `task_submitted`, `agent_token`, `agent_response`

These events operate at different abstraction levels and should **coexist,
not suppress each other**:

| Event Source | Audience | Example |
|-------------|----------|---------|
| Workflow engine | Workflow progress UI | "Step 2 of 3: Finding emails (running)" |
| Hub relay | Chat message stream | Token-by-token streaming, final response bubble |

The frontend should render workflow progress in a dedicated workflow status
component (step tracker) while hub relay events render in the normal chat
message stream. A `workflow_execution_id` field in the relay events allows
the frontend to correlate them — e.g., to show "this agent response belongs
to workflow step 2."

### Shared Agent Model (Canonical Reference)

> **This is the single authoritative definition of all planned additions
> to the `Agent` model.** The current model is in `models/agent.py`. The
> fields below will be added incrementally across Hub Phase 2 and Trust
> Layer Phase 0. Both sibling documents
> ([HYBRO_TRUST_LAYER_DESIGN.md §3.4](./HYBRO_TRUST_LAYER_DESIGN.md),
> [WORKFLOW_ENGINE_ROADMAP.md §7.2](./WORKFLOW_ENGINE_ROADMAP.md)) reference
> this section as the canonical source.

```python
# models/agent.py — proposed additions
class Agent(BaseModel):
    # === Existing fields (unchanged) ===
    agent_id: str
    provider_id: str | None = None
    agent_card: AgentCard              # from a2a.types — external, read-only
    normalized_url: str | None = None
    public_url: str | None = None
    agent_status: AgentStatus = AgentStatus.active
    call_count: int = 0
    call_success_count: int = 0
    like_count: int = 0
    dislike_count: int = 0
    rate_limit_per_user_per_hour: int | None = None
    rate_limit_system_per_hour: int | None = None
    is_public: bool = True

    # === From Hub Design §5.1 (added in Hub Phase 2a) ✅ ===
    source: str = "cloud"              # "cloud" | "hub"
    hub_id: str | None = None
    hub_owner_id: str | None = None
    is_hub_online: bool = False
    local_agent_id: str | None = None  # hub-assigned id, dedup key with hub_id

    # === From Trust Layer §3.4 (added in Trust Phase 0) ===
    identity: AgentIdentity | None = None
```

The `Workflow` model (WORKFLOW_ENGINE_ROADMAP.md §2a) does not extend `Agent`
— it references agents by `agent_id` in workflow step definitions.

### Implementation Sequencing Across Documents

The three designs have independent timelines. Below is the recommended
sequencing based on data model dependencies and shared infrastructure:

```
Quarter 1                          Quarter 2                     Quarter 3+
──────────────────────────────── ─────────────────────────────  ────────────
Workflow Phase 1 (Robust V2)     Workflow Phase 2 (Templates)   Workflow Phase 3-5
  └─ no model changes             └─ new Workflow/Execution     (fan-out, approval)
                                     models (independent)

Hub Phase 1 (Gateway API + SDK)  Hub Phase 2b (Hub Daemon)      Hub Phase 3
  └─ no Agent model changes ✅     └─ relay client, agent        (desktop app, etc.)
  └─ COMPLETE                       registry, dispatcher,
                                    privacy router, CLI,
Hub Phase 2a (Relay + Middleware)   Ollama adapter ✅
  └─ adds source/hub_id/         └─ COMPLETE
     local_agent_id to Agent ✅  Hub Phase 2c (Frontend) ✅
  └─ DispatchMiddleware arch ✅    └─ source badges, hub
  └─ COMPLETE                       status, offline styling,
                                    privacy badges, hub
                                    settings section
                                  └─ COMPLETE

Trust Phase 0 (Identity + HCT)  Trust Phase 1 (Policy + OTel)  Trust Phase 2-3
  └─ adds identity to Agent        └─ adds Cedar middleware to   (DPoP, enterprise)
  └─ can start in parallel          AgentMessageProcessor
     with Hub Phase 1             └─ uses middleware
                                     architecture from 2a ✅
```

**Key dependency**: Hub Phase 2a and Trust Phase 0 both extend the `Agent`
model. They can proceed in parallel (different fields, no conflict) but
should coordinate on a single migration. The middleware architecture
(implemented in Phase 2a) is ready for Trust Phase 1 to add Cedar
evaluation to `AgentMessageProcessor`.
