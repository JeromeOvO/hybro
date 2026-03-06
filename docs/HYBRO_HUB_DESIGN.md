# Hybro Hub: Portal-First Hybrid Agent Architecture

**Status:** Draft v3
**Date:** 2026-03-02
**Author:** Architecture Design

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
9. [User Journey](#9-user-journey)
10. [Phased Implementation Roadmap](#10-phased-implementation-roadmap)
11. [Competitive Landscape](#11-competitive-landscape)
12. [Risks & Mitigations](#12-risks--mitigations)
13. [Open Questions](#13-open-questions)

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
| Local storage | Not needed for primary UX | Rooms, messages, and history live in the cloud (MongoDB). Hub is stateless. |
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
│   ├── main.py              # Startup, relay connection, event loop
│   ├── config.py            # YAML config loader + env vars
│   ├── relay_client.py      # Outbound SSE subscription + HTTP publish to hybro.ai
│   ├── agent_registry.py    # Discover and health-check local A2A agents
│   ├── dispatcher.py        # A2A client — dispatch tasks to local agents
│   ├── privacy_router.py    # Sensitivity classification + routing decisions
│   └── gateway_client.py    # Call cloud agents via hybro.ai gateway API
├── config.yaml              # User configuration file
└── pyproject.toml
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
2. **Auto-discovery**: scan localhost ports 8000-8100 for
   `/.well-known/agent-card.json` (A2A standard discovery)
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
| `POST /api/v1/gateway/agents/discover` | Wraps `DiscoveryService.discover_agents()` |
| `POST /api/v1/gateway/agents/{id}/message/send` | Wraps `A2AService.send_message_sync()` |
| `POST /api/v1/gateway/agents/{id}/message/stream` | Wraps `A2AService.send_message_streaming()` |
| `GET /api/v1/gateway/agents/{id}/card` | Returns cached `AgentCard` from MongoDB |

### 4.2 Security

- **Authentication:** API key validated via `common/api_key_auth.py`. The
  `APIKey` model already has a `user_id` field.
- **Agent URL masking:** The hub only sees `agent_id`. The gateway resolves
  the real URL internally, protecting agent providers.
- **Rate limiting:** Per-user limits via existing `RateLimitService`.
- **Request validation:** Payloads validated against A2A schema before forwarding.

### 4.3 Relationship to Existing Backend

The gateway adds **new API routes** to `multi-agents-backend`. It reuses
`A2AService`, `DiscoveryService`, `AgentService`, `RateLimitService`, and
`api_key_auth`. No changes to the orchestration layer are needed — the
gateway is a proxy, not an orchestrator.

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
    agent_card: AgentCard
    # ... existing fields ...

    # Hub-sourced agent fields
    source: str = "cloud"          # "cloud" | "hub"
    hub_id: str | None = None
    hub_owner_id: str | None = None
    is_hub_online: bool = False
```

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

1. User opens hybro.ai, sends a message targeting a hub agent.
2. Backend's `sendMessage` detects `agent.source == "hub"`.
3. Instead of triggering `room_message_center.process_room_user_message`,
   it pushes a `user_message` event to the hub's relay SSE queue.
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

### 5.6 Offline Handling

- If the hub's SSE drops, relay marks all hub agents as `is_hub_online = false`
- Frontend's next `getAllActiveAgents` fetch shows them grayed out
- User messages to offline hub agents are queued with `pending_for_hub` flag
- When hub reconnects, queued messages are delivered
- UI shows: "Hub offline — messages will be delivered when your hub reconnects"
- **No fallback to cloud orchestration** — respects user's privacy choice

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

### 7.2 Frontend Changes (Minimal)

Since the portal-first approach keeps the user on hybro.ai at all times, the
frontend changes are small compared to the v2 dual-mode design:

1. **Agent source badge**: In agent list and chat bubbles, show 🏠 or ☁️ based
   on `agent.source`. The existing `allAgentsQuery` already fetches all agents;
   hub agents appear automatically once synced to MongoDB.

2. **Hub status indicator**: A status dot in settings or header. Data comes from
   a new field on the user profile or a lightweight `GET /api/v1/relay/hub/status`
   endpoint.

3. **Hub setup page**: A new settings tab ("My Hub") with install instructions,
   API key generation, and connected hub status. Shows the hub's synced agents.

4. **Offline agent styling**: When `agent.is_hub_online == false`, gray out the
   agent with tooltip: "Your hub is offline. Start your hub to use this agent."

5. **Privacy badge on messages**: A small badge component on `message-bubble.tsx`
   showing "Local" or "Cloud" based on SSE event metadata.

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

All agent communication uses A2A. Local-to-local, local-to-cloud, cloud-to-cloud.

| Capability | Usage |
|------------|-------|
| `message/send` | Synchronous dispatch to local and cloud agents |
| `message/stream` | Streaming dispatch (real-time token output) |
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

## 9. User Journey

### 9.1 Discovery

User is an existing hybro.ai user. They see a banner: "Run agents on your own
machine. Keep your data private." Or they find a "My Hub" tab in settings.

### 9.2 Setup (~5 minutes)

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

### 9.3 First Chat

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

### 9.4 Daily Use

- User opens `hybro.ai` on any device. Hub is running in background.
- Local and cloud agents are mixed in rooms. Routing is transparent.
- From phone: local agents work if hub is online. Otherwise grayed out.
- Cloud agents always work regardless of hub status.

### 9.5 Error Scenarios

| Scenario | What User Sees |
|----------|---------------|
| Hub crashes mid-conversation | "⚠️ Your hub disconnected. Response may be incomplete." Cloud agents still work. |
| User sends to offline hub agent | "📨 Message queued — will be delivered when your hub comes online" |
| Slow local agent (30s+ response) | Normal processing spinner with "🏠 Processing on My MacBook Pro..." |
| Hub API key revoked | Hub agents disappear from agent list. Hub logs auth error. |

---

## 10. Phased Implementation Roadmap

### Phase 1: Gateway API + SDK (4–6 weeks)

**Goal:** Enable local agents to discover and call cloud agents via hybro.ai.

**Deliverables:**
1. Gateway API endpoints in `multi-agents-backend` (discover, send, stream, card)
2. API key auth with `user_id` mapping
3. Python SDK (`hybro-sdk` PyPI package):
   ```python
   from hybro import HybroGateway
   gw = HybroGateway(api_key="hba_...")
   agents = await gw.discover("legal contract review")
   async for event in gw.stream(agents[0].agent_id, "Review..."):
       print(event)
   ```
4. Documentation: "Connect your local agent to hybro.ai's agent ecosystem"

**Validation:** 10+ developers use the SDK to call cloud agents from local code.

### Phase 2: Hub + Relay MVP (6–8 weeks)

**Goal:** Users install a hub, their local agents appear on hybro.ai.

**Deliverables:**
1. **Hub daemon** (`hybro-hub` PyPI package)
   - Relay client (SSE subscribe + HTTP publish)
   - Agent registry (manual config + auto-discovery)
   - Simple dispatcher (A2A client for local agents)
   - Privacy router v1 (keyword + regex)
   - Bundled Ollama A2A wrapper (`hybro-hub agent start ollama`)

2. **Relay service** (backend)
   - `POST /api/v1/relay/hub/register`
   - `GET /api/v1/relay/hub/{hub_id}/events` (SSE to hub)
   - `POST /api/v1/relay/hub/{hub_id}/publish` (events from hub)
   - `POST /api/v1/relay/hub/{hub_id}/agents/sync`
   - Agent model: `source`, `hub_id`, `hub_owner_id`, `is_hub_online` fields

3. **sendMessage path split** (backend)
   - When target agent has `source == "hub"`, route via relay instead of
     cloud orchestrator

4. **Frontend updates** (~300 lines)
   - Agent source badge (🏠 / ☁️)
   - Hub status indicator
   - Hub setup page in settings
   - Offline agent styling
   - Privacy badge on messages

**Validation:** User runs `hybro-hub start`, opens hybro.ai, chats with a
local Ollama agent alongside cloud agents.

### Phase 3: Advanced Features (12+ weeks, parallel)

1. **Desktop app** — Tauri wrapper with system tray, auto-start
2. **Privacy router v2** — LLM-based classification, reversible anonymization
3. **History sync** — Opt-in encrypted sync to cloud (E2E encrypted)
4. **Enterprise** — SSO, audit trails, hub fleet management, VPC deployment

---

## 11. Competitive Landscape

### 11.1 Market Map

| Product | What They Do | Overlap with Hybro Hub | Key Difference |
|---------|-------------|----------------------|----------------|
| **Clarifai Local Runners** | "Ngrok for AI models." Local model connects outbound to cloud API. | Same relay pattern, same privacy benefit. | Models only, not agents. No orchestration, no marketplace. |
| **Pryx** | Sovereign AI agent control center. Rust binary, local-first, 22+ providers. | Same philosophy (local + cloud, privacy). | CLI/TUI tool, not a web portal. No agent marketplace. No relay. |
| **SmythOS Studio** | Visual agent builder with local/cloud/enterprise deployment. | Same "one product, any deployment" model. | Build tool, not a bridge. No mixing local + cloud in one conversation. |
| **AgentCenter** | Dashboard for OpenClaw agents across distributed infra. | Same "one portal, agents everywhere" concept. | Task management, not conversational chat. Locked to OpenClaw. |
| **Microsoft Foundry Local** | Hybrid AI: local LLM + Azure agents via MCP. | Same hybrid concept, uses A2A + MCP. | Developer SDK, not a web portal. Azure-locked. Cloud initiates connections to local. |
| **Dify / n8n / Langflow** | Self-hosted workflow builders supporting Ollama + cloud APIs. | Support local models alongside cloud. | No mixing in one conversation. Self-hosted = all local or all cloud. |

### 11.2 The Gap

No product combines: (1) a cloud web portal where local and cloud agents
appear side-by-side, (2) conversational real-time chat, (3) privacy-based
routing, (4) an agent marketplace, and (5) outbound-only relay. Clarifai
validates the architecture, Pryx validates the philosophy, AgentCenter
validates the dashboard concept. Hybro Hub assembles all pieces.

---

## 12. Risks & Mitigations

### 12.1 Technical Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Relay latency** (4+ network hops for local agent messages) | Noticeable delay vs direct cloud agents | Streaming passthrough (don't buffer). Show "via your hub" indicator so users understand. Most local agent value is privacy, not speed. |
| **Hub offline = local agents unavailable** | Frustrating when user forgets to start hub | Desktop app with auto-start (Phase 3). Clear "Hub offline" indicators. Cloud agents always work as fallback. |
| **Agent sync consistency** | Stale agent list if hub disconnects without cleanup | TTL on hub agents (mark offline after 60s heartbeat miss). Hub re-syncs on reconnect. |
| **Event format mismatch** between hub publish and backend SSE | Broken chat rendering | Strict event schema validation in relay. Integration tests covering full round-trip. |

### 12.2 Product Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Cold start problem** (hub does nothing without local agents) | Users install hub, see no value | Bundle Ollama A2A wrapper. `hybro-hub agent start ollama` gives instant local agent. |
| **Privacy theatre** (users assume privacy but data may leak) | Trust damage | Open-source hub. Privacy badges per message. Network traffic dashboard in hub CLI. |
| **Too complex for non-developers** | Small user base | Phase 1 targets developers. Desktop app (Phase 3) simplifies. One-click setup. |

### 12.3 Business Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Hub cannibalizes cloud revenue** | Users stop using cloud agents | Cloud agents offer capabilities local can't match. Hub drives discovery of cloud agents. |
| **Support burden** of local software | High support costs | Docker as escape hatch. Community forums. Phase 1 targets self-serve developers. |

---

## 13. Open Questions

### 13.1 [OPEN] Hybrid Orchestration

When a user's message could benefit from both local and cloud agents (e.g.,
"research X and apply to my private data"), who orchestrates? Options:
- Hub orchestrator splits the task (more complex hub)
- User explicitly picks an agent per message (simpler but manual)
- Cloud orchestrator with privacy-aware routing (requires cloud to understand
  which data is sensitive)

Phase 2 starts with explicit agent selection. Hybrid orchestration is Phase 3.

### 13.2 [OPEN] Room-Level vs Message-Level Agent Targeting

Current hybro.ai assigns agents to rooms (`room_agent_set`). With hub agents,
should the user:
- Assign agents to rooms as today (but now including local agents)?
- Pick per message which agent to use?
- Let the orchestrator decide automatically?

Phase 2: add local agents to `room_agent_set` same as cloud agents. The
frontend's "Add agents" flow works unchanged — hub agents just appear in
the available agents list.

### 13.3 [OPEN] Streaming Shortcut

The relay adds latency: local agent → hub → relay POST → backend SSE →
browser. For real-time token streaming, consider: hub sends tokens directly
to a WebSocket/SSE endpoint instead of batching via REST POST. This is an
optimization for Phase 3.

### 13.4 [OPEN] Air-Gapped Mode Scope

The air-gapped mode (§2.2 Mode 3) is listed as an escape hatch. How much
should we invest in it? Options:
- Minimal: hub serves a health/status endpoint on localhost. No chat UI.
- Moderate: hub serves a simple chat TUI in the terminal.
- Full: hub serves a local web UI (back to v2 complexity).

Recommendation: minimal for Phase 2. Revisit based on demand.

### 13.5 [OPEN] Multi-Hub Coordination

A user with multiple hubs (laptop + desktop). Both are online. A room has
agents from both hubs. When the user sends a message, which hub processes it?
The `agent_id` encodes which hub owns it, so routing is per-agent, not per-hub.
But cancellation, status, and ordering need careful design.
