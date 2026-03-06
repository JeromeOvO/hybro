# Hybro Trust Layer: Identity, Authorization, and Governance for A2A Agents

**Status:** Draft v1
**Date:** 2026-03-02
**Author:** Architecture Design
**Depends on:** [HYBRO_HUB_DESIGN.md](./HYBRO_HUB_DESIGN.md) (Portal-First Hub Architecture)

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [A2A Security Gap Analysis](#2-a2a-security-gap-analysis)
3. [Cryptographic Agent Identity](#3-cryptographic-agent-identity)
4. [Scoped Capability Tokens](#4-scoped-capability-tokens)
5. [Execution Transparency](#5-execution-transparency)
6. [Declarative Trust Policies](#6-declarative-trust-policies)
7. [Integration with Hybro Hub](#7-integration-with-hybro-hub)
8. [Phased Implementation Roadmap](#8-phased-implementation-roadmap)
9. [Risks & Open Questions](#9-risks--open-questions)

---

## 1. Problem Statement

The A2A (Agent-to-Agent) protocol defines how agents discover each other,
exchange messages, and manage tasks. It is a strong **interaction** protocol.
It is a weak **trust** protocol.

As hybro.ai connects local agents with cloud agents across organizational
boundaries, three questions arise that A2A does not answer:

1. **Who is this agent?** — A2A identifies agents by URL and AgentCard. URLs
   can be spoofed. AgentCards are self-declared. There is no cryptographic
   proof that an agent is who it claims to be.

2. **What is this agent allowed to do?** — A2A defers to "standard web
   security practices" (Bearer tokens, OAuth2) but defines no agent-specific
   scoping. A token that grants "read access" doesn't express "read access
   to this specific contract, for this specific task, expiring in 15 minutes."

3. **What happened, and can I prove it?** — A2A has no logging, tracing, or
   audit trail. When Agent A calls Agent B which calls Agent C, there is no
   standard way to trace the chain, verify what data was sent, or hold any
   party accountable.

Hybro introduces four primitives to fill these gaps — built on mature
standards, layered on top of A2A without breaking compatibility.

---

## 2. A2A Security Gap Analysis

### 2.1 What A2A Provides

| Feature | A2A Support | How |
|---------|:---:|-----|
| Agent discovery | Yes | AgentCard at `/.well-known/agent-card.json` |
| Capability declaration | Yes | `skills` array in AgentCard |
| Transport security | Yes | HTTPS |
| Auth scheme declaration | Yes | `securitySchemes` in AgentCard (Bearer, OAuth2, API key) |
| Task lifecycle | Yes | `tasks/send`, `tasks/get`, `tasks/cancel` with state machine |
| Streaming | Yes | SSE for real-time updates |
| Push notifications | Yes | Webhook callbacks with token verification |

### 2.2 What A2A Does Not Provide

| Gap | Impact | Academic Reference |
|-----|--------|-------------------|
| **No cryptographic agent identity** | Agents are identified by URL, not by verified identity. AgentCards are self-declared claims with no proof. | "A2A relies primarily on Claim-based trust... brittle against prompt injection, sycophancy, hallucination" (arxiv 2511.03434) |
| **No fine-grained permission scoping** | Auth tokens (Bearer, OAuth2) are coarse. No task binding, no time scoping, no delegation tracking. | "Insufficient token lifetime control, overbroad access scopes, missing consent flows" (arxiv 2505.12490) |
| **No audit trail or tracing** | No standard for logging cross-agent interactions. No trace context propagation. No tamper-evident records. | EU AI Act requires traceability for high-risk systems. HIPAA mandates tracking of all data access by AI agents. |
| **No organizational policy enforcement** | No mechanism for an organization to declare "only allow agents with X capabilities from Y partners." | "No single trust mechanism suffices... recommend hybrid architectures anchored in cryptographic proof" (arxiv 2511.03434) |

### 2.3 Design Approach

Hybro's trust layer is **additive, not forking**. It adds fields to A2A
structures (AgentCard extensions, JWT claims, HTTP headers) without changing
the A2A wire protocol. An agent that doesn't support the trust layer can
still interact via standard A2A — it just won't receive trust-gated tasks.

---

## 3. Cryptographic Agent Identity

### 3.1 The Problem

A2A's AgentCard includes `name`, `description`, `url`, and `provider`. All
self-declared. Agent B has no way to verify that Agent A's card is authentic
beyond trusting the URL's TLS certificate — which proves domain ownership,
not agent identity.

In a cross-org scenario (Company X's agent calling Company Y's agent via
Hybro), this is insufficient. Company Y needs to know: Is this really
Company X's agent? Has it been tampered with? Is it authorized to act on
behalf of Company X?

### 3.2 Solution: DID + X.509 Dual Identity

Each agent registered on Hybro receives a dual identity:

**W3C Decentralized Identifier (DID)**
```
did:hybro:agent:abc123
```

The DID resolves to a DID Document containing the agent's public key,
service endpoints, and verification methods. DIDs are the W3C standard
(W3C Recommendation, 2022) for decentralized identity — mature, widely
tooled, and protocol-agnostic.

**X.509 Certificate**

For enterprise environments that require PKI integration, each agent also
receives an X.509 certificate signed by Hybro's Certificate Authority (or
the organization's own CA for enterprise deployments). This provides
compatibility with existing enterprise security infrastructure (mTLS,
certificate pinning, LDAP).

### 3.3 Key Components

| Component | Purpose | Storage |
|-----------|---------|---------|
| `agent_did` | W3C DID identifier | MongoDB `Agent` model, AgentCard extension |
| `public_key` | ED25519 public key for verification | DID Document + Agent model |
| `private_key` | ED25519 private key for signing | Hub local keystore (`~/.hybro/keys/`) or cloud HSM |
| `x509_cert` | X.509 certificate (PEM) | Agent model, served at `/.well-known/agent-cert.pem` |
| `cert_chain` | CA chain for certificate validation | Hybro CA or enterprise CA |

### 3.4 AgentCard Extension

The trust layer extends A2A's AgentCard with an optional `identity` block:

```json
{
  "name": "Legal Contract Reviewer",
  "url": "https://agents.partner-corp.com/legal",
  "provider": { "organization": "Partner Corp", "url": "https://partner-corp.com" },
  "skills": [...],
  "securitySchemes": [...],

  "identity": {
    "did": "did:hybro:agent:abc123",
    "publicKey": {
      "type": "Ed25519VerificationKey2020",
      "publicKeyMultibase": "z6Mkf5rGMoatrSj1f..."
    },
    "x509CertificateUrl": "https://agents.partner-corp.com/.well-known/agent-cert.pem",
    "verificationLevel": "organization"
  }
}
```

Agents without the `identity` block are treated as **unverified** — they can
still interact via standard A2A, but trust policies (§6) can restrict what
tasks they receive.

### 3.5 Verification Levels

| Level | Meaning | How Achieved | Trust |
|-------|---------|-------------|-------|
| **Unverified** | No identity claims | Agent has no `identity` block | Lowest — treated as anonymous |
| **Self-signed** | Agent has a key pair but no CA backing | Agent generates own DID + keys | Low — proves consistency, not identity |
| **Platform-verified** | Hybro has verified the agent's owner | Hybro CA signs the certificate during registration | Medium — Hybro vouches for the agent |
| **Organization-verified** | The agent's organization is independently verified | Enterprise CA or third-party verification (e.g., Vouched Agent Checkpoint) | Highest — suitable for cross-org |

### 3.6 Verification Flow

When Agent A (caller) wants to verify Agent B (callee):

```
Agent A                              Hybro / Agent B
   │                                      │
   ├── GET AgentCard ────────────────────→│
   │←── AgentCard (with identity block) ──┤
   │                                      │
   ├── Resolve DID ──────────────────────→│  (Hybro DID resolver)
   │←── DID Document (public key) ────────┤
   │                                      │
   ├── Challenge: sign this nonce ───────→│
   │←── Signed nonce ─────────────────────┤
   │                                      │
   ├── Verify signature against           │
   │   public key from DID Document       │
   │                                      │
   │  ✓ Identity verified                 │
```

For high-throughput scenarios, the challenge-response is replaced by JWT
verification: Agent B presents a short-lived JWT signed by its private key
(similar to OpenAgents' Level 2 verification). The caller validates the JWT
signature against the public key from the DID Document.

### 3.7 Implementation: What to Build

| Component | Technology | Effort |
|-----------|-----------|--------|
| DID resolver | `did:hybro` method — HTTP-based resolution via `GET /api/v1/identity/{did}` returning DID Document | Low |
| Key generation | ED25519 via Python `cryptography` library | Low |
| Certificate issuance | Hybro CA using `cryptography.x509` — self-signed root CA, per-agent leaf certs | Medium |
| AgentCard extension | Add optional `identity` field to Agent model in MongoDB | Low |
| Verification middleware | FastAPI middleware that validates identity on incoming A2A requests | Medium |
| Hub key management | Store private keys in `~/.hybro/keys/` with file permissions. Enterprise: HSM via PKCS#11. | Low (file) / High (HSM) |

---

## 4. Scoped Capability Tokens

### 4.1 The Problem

A2A delegates authorization to "standard web security" — Bearer tokens,
API keys, OAuth2. These mechanisms were designed for human users accessing
APIs, not for autonomous agents delegating work to other agents.

The gaps:

- **No task binding** — A Bearer token grants access to an endpoint, not to a
  specific task. Agent B receives a token that says "read financial data" but
  nothing constrains it to the particular analysis task requested by Agent A.

- **No delegation tracking** — When Agent A calls Agent B which calls Agent C,
  the token Agent C receives has no trace of the delegation chain. If Agent C
  misbehaves, there's no proof of how it got the token.

- **No time scoping** — OAuth2 access tokens have expiry, but not task-aware
  expiry ("valid only for the duration of task T-12345").

- **No capability narrowing** — When Agent A delegates to Agent B, the token
  should be *narrower* than Agent A's own permissions. Standard OAuth2 has
  no built-in mechanism for progressive scope reduction.

### 4.2 Why Not AAP (Agent Authorization Profile)?

The IETF's Agent Authorization Profile draft proposes extending OAuth 2.0
with agent-specific claims. However:

| Concern | Status |
|---------|--------|
| IETF draft maturity | Individual draft, not adopted by a working group |
| Implementation count | Zero known production deployments |
| SDK/library support | None |
| Specification stability | Subject to breaking changes |

**Verdict:** AAP's concepts are sound, but the draft is too immature to
depend on. We adopt the same *goals* using mature, production-proven
standards: **OAuth 2.0 + JWT + custom claims**.

### 4.3 Solution: Hybro Capability Tokens (HCT)

A Hybro Capability Token is a standard JWT with a well-defined claim
namespace (`hybro:*`) that encodes agent-specific authorization context.

#### Token Structure

```json
{
  "iss": "https://hybro.ai",
  "sub": "did:hybro:agent:caller-123",
  "aud": "did:hybro:agent:callee-456",
  "iat": 1740873600,
  "exp": 1740877200,

  "hybro:task_id": "task-789",
  "hybro:room_id": "room-abc",
  "hybro:scopes": [
    "financial:read",
    "contracts:summarize"
  ],
  "hybro:max_delegation_depth": 2,
  "hybro:delegation_chain": [
    "did:hybro:agent:orchestrator-000"
  ],
  "hybro:data_classification": "confidential",
  "hybro:binding": {
    "type": "DPoP",
    "jkt": "sha256-thumbprint-of-callee-public-key"
  }
}
```

#### Claim Definitions

| Claim | Type | Purpose |
|-------|------|---------|
| `sub` | string (DID) | The agent requesting access (caller) |
| `aud` | string (DID) | The target agent (callee) |
| `hybro:task_id` | string | Binds the token to a specific A2A task. The callee MUST reject the token if the task ID doesn't match. |
| `hybro:room_id` | string | Binds the token to a specific Hybro room/conversation context. |
| `hybro:scopes` | string[] | Fine-grained permission scopes. Format: `resource:action` (e.g., `financial:read`, `contracts:summarize`). |
| `hybro:max_delegation_depth` | integer | Maximum allowed delegation chain length. Decremented on each delegation. 0 means "no further delegation allowed." |
| `hybro:delegation_chain` | string[] (DIDs) | Ordered list of agents in the delegation path. Appended on each hop. |
| `hybro:data_classification` | string | Maximum data sensitivity level the callee may access: `public`, `internal`, `confidential`, `restricted`. |
| `hybro:binding` | object | Proof-of-Possession binding (DPoP) — ties the token to the callee's key pair, preventing token theft. |

### 4.4 Token Lifecycle

```
User / Orchestrator          Hybro Token Service          Callee Agent
       │                            │                          │
       ├── Request token ──────────→│                          │
       │   (scopes, task_id,        │                          │
       │    target_agent_did)       │                          │
       │                            │                          │
       │   ┌─ Validate request ─────┤                          │
       │   │  - caller identity ✓   │                          │
       │   │  - scopes ⊆ caller's   │                          │
       │   │    allowed scopes ✓    │                          │
       │   │  - policy check (§6) ✓ │                          │
       │   └────────────────────────┤                          │
       │                            │                          │
       │←── HCT (signed JWT) ──────┤                          │
       │                            │                          │
       ├── A2A request + HCT ─────────────────────────────────→│
       │   (Authorization: Bearer <HCT>)                       │
       │                            │                          │
       │                            │   ┌─ Validate HCT ──────┤
       │                            │   │  - signature ✓       │
       │                            │   │  - aud matches ✓     │
       │                            │   │  - task_id matches ✓ │
       │                            │   │  - not expired ✓     │
       │                            │   │  - DPoP binding ✓    │
       │                            │   └──────────────────────┤
       │                            │                          │
       │←── A2A response ─────────────────────────────────────┤
```

### 4.5 Delegation and Scope Narrowing

When Agent B needs to call Agent C on behalf of the original task:

1. Agent B requests a new HCT from the Token Service.
2. The Token Service enforces:
   - `hybro:scopes` MUST be a **subset** of Agent B's received scopes.
   - `hybro:max_delegation_depth` is decremented by 1. If already 0, delegation is denied.
   - `hybro:delegation_chain` is appended with Agent B's DID.
3. The new HCT is issued for Agent C.

This creates a **monotonically narrowing permission chain**: each hop can
only have equal or fewer permissions than the previous hop.

```
Orchestrator (scopes: [financial:read, financial:write, contracts:*])
    │
    └─→ Agent B (scopes: [financial:read, contracts:summarize])
            │      (narrowed: dropped financial:write, narrowed contracts)
            │
            └─→ Agent C (scopes: [financial:read])
                   (narrowed further: dropped contracts:summarize)
```

### 4.6 Proof of Possession (DPoP)

Standard Bearer tokens can be stolen and replayed. For agent-to-agent
communication, this is a real risk — tokens transit through message buses,
relay services, and potentially compromised agents.

Hybro adopts **DPoP (Demonstrating Proof of Possession)**, RFC 9449:

1. The HCT includes a `hybro:binding.jkt` claim — the SHA-256 thumbprint
   of the callee's public key (from its DID Document).
2. When the callee presents the token, it also presents a DPoP proof —
   a short-lived JWT signed by its private key.
3. The validator checks that the DPoP proof's key matches the `jkt` in the
   HCT.

This ensures that even if a token is intercepted, it cannot be used by
anyone other than the intended callee.

### 4.7 Token Service Architecture

The Token Service is a new component in the Hybro backend:

```
POST /api/v1/tokens/issue
Authorization: Bearer <caller's auth token>

{
  "target_agent_did": "did:hybro:agent:callee-456",
  "task_id": "task-789",
  "room_id": "room-abc",
  "requested_scopes": ["financial:read", "contracts:summarize"],
  "ttl_seconds": 3600
}

→ 200 OK
{
  "token": "eyJhbGciOiJFZERTQ...",
  "expires_at": "2026-03-01T11:00:00Z"
}
```

The Token Service:
- Validates the caller's identity (via DID or existing auth).
- Checks the requested scopes against the caller's allowed scopes.
- Applies declarative trust policies (§6) for the target agent.
- Signs the HCT with Hybro's signing key (ED25519).
- Logs the issuance (§5 — Execution Transparency).

### 4.8 Scope Taxonomy

A standard scope vocabulary prevents fragmentation across agents:

| Scope Pattern | Meaning | Example |
|---------------|---------|---------|
| `{resource}:read` | Read-only access to a resource type | `financial:read` |
| `{resource}:write` | Create/modify a resource type | `documents:write` |
| `{resource}:delete` | Delete a resource type | `records:delete` |
| `{resource}:summarize` | Generate summaries (no raw data access) | `contracts:summarize` |
| `{resource}:analyze` | Run analysis (may include raw data) | `metrics:analyze` |
| `tools:{tool_name}` | Permission to invoke a specific MCP tool | `tools:web_search` |
| `agents:{agent_did}:invoke` | Permission to delegate to a specific agent | `agents:did:hybro:agent:789:invoke` |

Scopes are **deny-by-default**: if a scope is not explicitly included in
the HCT, the callee does not have that permission.

### 4.9 Implementation: What to Build

| Component | Technology | Effort |
|-----------|-----------|--------|
| Token Service | FastAPI endpoint (`/api/v1/tokens/issue`), ED25519 signing via `PyJWT` + `cryptography` | Medium |
| Token validation middleware | FastAPI dependency that validates HCT on incoming A2A requests | Medium |
| Scope registry | MongoDB collection mapping agent DIDs to allowed scopes | Low |
| DPoP verification | Validate DPoP proof JWTs per RFC 9449 | Medium |
| Delegation chain tracking | Append caller DID to chain, enforce max depth | Low |
| Hub token caching | Local cache in hub daemon to avoid re-requesting tokens for active tasks | Low |

---

## 5. Execution Transparency

### 5.1 The Problem

When Agent A calls Agent B which calls Agent C, several things happen that
nobody can currently prove:

- What data did Agent A send to Agent B?
- Did Agent B modify the request before forwarding to Agent C?
- How long did each hop take?
- Did any agent access data outside its authorized scope?
- If something goes wrong, which agent is responsible?

This matters for compliance (EU AI Act requires traceability for high-risk
AI systems, HIPAA mandates audit trails for health data access) and for
operational debugging (why did a 3-agent chain produce the wrong answer?).

A2A has **zero** support for tracing or audit logging. Each agent is a black
box.

### 5.2 Solution: Three-Layer Transparency

Execution transparency in Hybro operates at three layers:

```
┌─────────────────────────────────────────┐
│  Layer 3: Tamper-Evident Audit Log      │ ← Compliance, legal
│  (append-only, hash-chained records)    │
├─────────────────────────────────────────┤
│  Layer 2: Structured Event Logging      │ ← Ops, monitoring
│  (JSON events in a queryable store)     │
├─────────────────────────────────────────┤
│  Layer 1: Distributed Trace Context     │ ← Debugging, perf
│  (W3C Trace Context via OpenTelemetry)  │
└─────────────────────────────────────────┘
```

Each layer builds on the one below it.

### 5.3 Layer 1: Distributed Trace Context

Every A2A request carries W3C Trace Context headers (already a W3C
Recommendation since 2020):

```http
POST /tasks/send HTTP/1.1
Host: agent-b.example.com
Authorization: Bearer <HCT>
traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
tracestate: hybro=room_id:room-abc;task_id:task-789
```

| Header | Purpose |
|--------|---------|
| `traceparent` | Standard W3C header: version, trace ID (shared across all hops), span ID (unique per hop), flags |
| `tracestate` | Vendor-specific context. Hybro adds room ID and task ID for cross-referencing with business logic. |

**How it works:**

1. The orchestrator (or Hub) generates a trace ID when a user sends a message.
2. Each agent creates a child span when it receives a request and attaches
   the trace ID.
3. Spans are exported to an OpenTelemetry collector.
4. The collector feeds into Hybro's trace store (Jaeger, Grafana Tempo, or
   a lightweight in-house store).

**What this enables:**
- End-to-end latency breakdown across multi-agent chains.
- Identification of slow or failing agents.
- Visualization of the agent call graph for any task.

### 5.4 Layer 2: Structured Event Logging

Beyond trace spans (which capture timing and hierarchy), Hybro logs
**semantic events** that capture what happened at a business level:

```json
{
  "event_id": "evt-001",
  "timestamp": "2026-03-01T10:00:05Z",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "span_id": "00f067aa0ba902b7",

  "event_type": "agent.task.delegated",
  "actor": "did:hybro:agent:orchestrator-000",
  "target": "did:hybro:agent:financial-analyzer-456",

  "context": {
    "room_id": "room-abc",
    "task_id": "task-789",
    "scopes_granted": ["financial:read"],
    "delegation_depth": 1,
    "data_classification": "confidential"
  },

  "outcome": "accepted",
  "duration_ms": null
}
```

#### Event Taxonomy

| Event Type | When Emitted | Key Fields |
|------------|-------------|------------|
| `agent.task.created` | Orchestrator creates a new task | task_id, agent_did, scopes |
| `agent.task.delegated` | Agent delegates a sub-task to another agent | from_did, to_did, scopes_granted, delegation_depth |
| `agent.task.completed` | Agent completes a task | task_id, outcome (success/fail), duration_ms |
| `agent.task.cancelled` | Task is cancelled (by user or timeout) | task_id, reason, cancelled_by |
| `agent.data.accessed` | Agent accesses data of a given classification | agent_did, classification, scope_used |
| `agent.data.shared` | Data crosses an organizational boundary | from_org, to_org, classification, size_bytes |
| `agent.policy.denied` | A policy (§6) denied an action | agent_did, attempted_action, policy_id |
| `agent.token.issued` | An HCT is issued | subject_did, audience_did, scopes, ttl |
| `agent.token.rejected` | An HCT validation fails | reason (expired, scope_mismatch, dpop_fail) |
| `agent.identity.verified` | Agent identity verification completes | agent_did, level, method |

Events are stored in a **time-series optimized store** (MongoDB time-series
collection or ClickHouse for high-volume deployments).

### 5.5 Layer 3: Tamper-Evident Audit Log

For compliance-grade auditability, critical events (data access, cross-org
sharing, policy denials) are written to a **hash-chained append-only log**:

```
Record N:
  payload: { event_type: "agent.data.shared", ... }
  hash: SHA-256(payload + Record[N-1].hash)
  timestamp: 2026-03-01T10:00:05Z
  signature: <signed by Hybro's audit key>
```

Properties:
- **Append-only:** Records cannot be modified or deleted.
- **Hash-chained:** Each record includes the hash of the previous record.
  Any tampering breaks the chain and is detectable.
- **Signed:** Each record is signed by Hybro's audit key, proving the
  record was created by the system and not injected.
- **Verifiable:** Any party can verify the chain by recomputing hashes.

This is a lightweight version of blockchain-style integrity without the
overhead of consensus. For regulated environments, the log can be anchored
to a public blockchain (e.g., periodic root hash publication to Ethereum)
for third-party verifiability.

### 5.6 Trace Context in Hub ↔ Cloud Relay

When a local agent processes a task via the cloud relay:

```
Frontend → Backend → Relay → Hub → Local Agent
                                      │
trace_id generated ──────────────────→│
by backend                            │
                                      │
Local agent creates ←─────────────────┤
child span, exports                   │
to hub's local                        │
collector                             │

Hub batches spans ────────────────────→ Cloud collector
(via relay or direct OTLP export)
```

The hub daemon runs a lightweight OpenTelemetry collector sidecar that:
1. Receives spans from local agents via OTLP.
2. Batches and exports them to the cloud collector via the relay connection
   (no new outbound ports needed).
3. Optionally retains a local copy for air-gapped debugging.

### 5.7 User-Facing Transparency

Execution transparency isn't just for compliance teams. Users see it too:

- **Task trace view:** In the Hybro portal, users can click on any task and
  see the agent call graph — which agents were involved, how long each took,
  what scopes were used.
- **Data flow badges:** Messages in the chat show badges indicating whether
  data stayed local, went to a cloud agent, or crossed an organizational
  boundary.
- **Alert on policy denials:** If a task was partially blocked by a policy
  (e.g., an agent wasn't allowed to access confidential data), the user sees
  a clear notification with the reason.

### 5.8 Implementation: What to Build

| Component | Technology | Effort |
|-----------|-----------|--------|
| Trace context propagation | OpenTelemetry Python SDK (`opentelemetry-api`, `opentelemetry-sdk`) — instrument A2A client/server | Medium |
| Trace collector | OpenTelemetry Collector (Docker sidecar) or Grafana Alloy | Low (config) |
| Trace backend | Grafana Tempo (self-hosted) or Jaeger. For MVP: simple MongoDB storage of spans. | Low (MVP) / Medium (prod) |
| Event logging service | FastAPI service writing structured events to MongoDB time-series collection | Medium |
| Audit log | Append-only MongoDB collection with hash chaining. Python `hashlib` for SHA-256. | Medium |
| Hub OTel sidecar | Lightweight OTel collector in the hub daemon process | Medium |
| Task trace UI | React component in Hybro portal — call graph visualization (D3.js or react-flow) | High |
| Data flow badges | Frontend component reading event metadata from backend | Low |

---

## 6. Declarative Trust Policies

### 6.1 The Problem

Identity (§3) tells you **who** an agent is. Tokens (§4) tell you **what**
it's allowed to do right now. Policies tell you **the rules** — the
organizational decisions that govern which agents can interact, under what
conditions, with what data.

Without policies, every interaction requires manual decision-making:
"Should Agent A be allowed to call Agent B?" In a world with dozens of
agents across organizations, this doesn't scale.

Organizations need to declare rules like:
- "Only allow partner agents that are organization-verified."
- "Financial data agents must have `confidential`-level clearance."
- "No external agent may write to our HR system."
- "Allow Agent X to read contracts, but only during business hours."
- "Maximum delegation depth for any external agent is 2."

### 6.2 Solution: Cedar-Based Policy Engine

Hybro uses **Cedar** (developed by AWS, open-source, formally verified) as
its policy language. Cedar was chosen over alternatives for specific reasons:

| Engine | Pros | Cons | Verdict |
|--------|------|------|---------|
| **Cedar** | Formally verified (provably correct), purpose-built for authorization, clear deny/allow semantics, fast evaluation | Newer ecosystem, fewer community integrations | **Selected** — formal verification critical for security policies |
| OPA/Rego | Large community, flexible, Kubernetes-native | General-purpose (not auth-specific), Rego learning curve, no formal verification | Good for infrastructure policy, overkill for agent auth |
| Casbin | Simple model, many adapters | Limited expressiveness for complex agent scenarios | Too simple |

### 6.3 Policy Structure

A Hybro trust policy consists of:

```
┌─────────────────────────────────────────────┐
│                Trust Policy                  │
│                                              │
│  Scope:    org:partner-corp                  │
│  Target:   agent interactions                │
│                                              │
│  ┌───────────────────────────────────────┐   │
│  │  Rule 1: Allow read-only financial    │   │
│  │  Rule 2: Deny write to HR system      │   │
│  │  Rule 3: Require org-verified identity│   │
│  └───────────────────────────────────────┘   │
│                                              │
│  Enforcement: pre-request (block before      │
│               task is dispatched)             │
└─────────────────────────────────────────────┘
```

### 6.4 Cedar Policy Examples

**Allow partner agents with read-only financial access:**

```cedar
permit(
  principal is Hybro::Agent,
  action == Hybro::Action::"task.delegate",
  resource is Hybro::Agent
)
when {
  principal.organization == "partner-corp" &&
  principal.verification_level in ["platform_verified", "organization_verified"] &&
  context.scopes.containsAll(["financial:read"]) &&
  !context.scopes.containsAny(["financial:write", "financial:delete"])
};
```

**Deny all external agents from accessing HR data:**

```cedar
forbid(
  principal is Hybro::Agent,
  action == Hybro::Action::"data.access",
  resource is Hybro::DataStore
)
when {
  principal.organization != resource.owner_organization &&
  resource.category == "hr"
};
```

**Allow delegation, but limit depth for external agents:**

```cedar
permit(
  principal is Hybro::Agent,
  action == Hybro::Action::"task.delegate",
  resource is Hybro::Agent
)
when {
  principal.is_external == true &&
  context.delegation_depth <= 2
};
```

**Time-bound access (business hours only):**

```cedar
permit(
  principal is Hybro::Agent,
  action == Hybro::Action::"data.access",
  resource is Hybro::DataStore
)
when {
  principal.did == "did:hybro:agent:contractor-agent-789" &&
  context.request_hour >= 9 &&
  context.request_hour <= 17
};
```

### 6.5 Entity Model

Cedar operates on entities — principals, actions, and resources. Hybro
defines the following entity types:

| Entity Type | Represents | Key Attributes |
|-------------|-----------|----------------|
| `Hybro::Agent` | An agent (local or cloud) | `did`, `organization`, `verification_level`, `is_external`, `is_local`, `hub_id` |
| `Hybro::User` | A human user | `user_id`, `organization`, `roles` |
| `Hybro::DataStore` | A data resource category | `category` (financial, hr, contracts...), `owner_organization`, `classification` |
| `Hybro::Room` | A conversation context | `room_id`, `participants`, `data_classification` |
| `Hybro::Action` | An action being performed | `task.create`, `task.delegate`, `data.access`, `data.share`, `agent.invoke` |

### 6.6 Policy Evaluation Flow

```
Incoming A2A Request
        │
        ▼
┌──────────────────┐
│ Extract context:  │
│ - caller DID      │
│ - target DID      │
│ - action          │
│ - scopes          │
│ - data class.     │
│ - delegation depth│
└────────┬─────────┘
         │
         ▼
┌──────────────────┐     ┌──────────────────┐
│  Cedar Engine     │────→│  Policy Store     │
│  evaluate(        │     │  (MongoDB)        │
│    principal,     │     │                   │
│    action,        │     │  - org policies   │
│    resource,      │     │  - room policies  │
│    context        │     │  - global default │
│  )                │     └──────────────────┘
└────────┬─────────┘
         │
    ┌────┴────┐
    │         │
  ALLOW     DENY
    │         │
    ▼         ▼
 Proceed   Return 403 +
 with      log policy
 request   denial event (§5)
```

### 6.7 Policy Hierarchy

Policies are evaluated in a specific order. Cedar's default-deny semantics
mean that an explicit `forbid` always wins over a `permit`:

```
1. Global defaults (Hybro platform rules)
   └── e.g., "Deny all unverified agents from cross-org access"

2. Organization policies (set by org admins)
   └── e.g., "Allow partner-corp agents with read-only financial scope"

3. Room-level policies (set by room creator or org policy)
   └── e.g., "This room contains confidential data; only org-verified agents"

4. User preferences (personal overrides within org bounds)
   └── e.g., "I allow my local agent X to access my documents"
```

Higher-specificity policies override lower-specificity ones, but a `forbid`
at any level is absolute.

### 6.8 Policy Management API

```
POST /api/v1/policies
Authorization: Bearer <admin token>

{
  "policy_id": "pol-001",
  "name": "Partner Financial Read-Only",
  "scope": "organization",
  "organization_id": "org-partner-corp",
  "cedar_policy": "permit( principal is Hybro::Agent, ... ) when { ... };",
  "enabled": true,
  "created_by": "user-admin-123"
}
```

```
GET /api/v1/policies?scope=organization&org_id=org-partner-corp
→ List of all policies for the organization
```

```
POST /api/v1/policies/evaluate
{
  "principal_did": "did:hybro:agent:partner-agent-456",
  "action": "task.delegate",
  "resource_did": "did:hybro:agent:financial-analyzer-789",
  "context": {
    "scopes": ["financial:read"],
    "delegation_depth": 1,
    "data_classification": "confidential"
  }
}
→ { "decision": "allow", "matched_policies": ["pol-001"] }
```

### 6.9 Policy for Hub-Local Agents

Local agents registered via the Hub have special policy considerations:

- **Implicit trust:** A user's own local agents are implicitly trusted for
  that user's data (no policy required for same-user access).
- **Cross-user access:** When a local agent serves data to another user's
  agent (e.g., in a shared room), the same cross-org policies apply.
- **Offline fallback:** If the hub is disconnected from the cloud, the hub
  caches the latest policy snapshot and evaluates locally. Stale policies
  are flagged in the audit log when reconnection occurs.

### 6.10 Implementation: What to Build

| Component | Technology | Effort |
|-----------|-----------|--------|
| Cedar engine integration | `cedarpy` (Python bindings for Cedar) | Medium |
| Policy store | MongoDB collection with Cedar policy text + metadata | Low |
| Policy evaluation middleware | FastAPI middleware called before A2A dispatch, invokes Cedar engine | Medium |
| Policy management API | CRUD endpoints for policies with org-level access control | Medium |
| Policy UI | React admin page: create/edit/test policies with live preview | High |
| Policy sync to hub | Hub caches policies from cloud relay for offline evaluation | Medium |
| Policy testing tool | CLI/UI tool to simulate policy evaluation without live requests | Low |

---

## 7. Integration with Hybro Hub Architecture

The trust layer is designed to integrate with the Portal-First Hub
architecture described in [HYBRO_HUB_DESIGN.md](./HYBRO_HUB_DESIGN.md).

### 7.1 How the Four Primitives Map to Hub Components

```
                        ┌──────────────────────────────┐
                        │      hybro.ai Web Portal     │
                        │                              │
                        │  Trust UI:                   │
                        │  - Task trace viewer         │
                        │  - Policy admin panel        │
                        │  - Agent verification badges │
                        │  - Data flow indicators      │
                        └──────────┬───────────────────┘
                                   │
                        ┌──────────▼───────────────────┐
                        │     Hybro Cloud Backend      │
                        │                              │
                        │  ┌─────────────────────────┐ │
                        │  │ Identity Service        │ │ ← §3
                        │  │ (DID resolver, CA,      │ │
                        │  │  verification)          │ │
                        │  ├─────────────────────────┤ │
                        │  │ Token Service           │ │ ← §4
                        │  │ (HCT issuance,          │ │
                        │  │  validation, DPoP)      │ │
                        │  ├─────────────────────────┤ │
                        │  │ Transparency Service    │ │ ← §5
                        │  │ (OTel collector, event  │ │
                        │  │  logger, audit log)     │ │
                        │  ├─────────────────────────┤ │
                        │  │ Policy Engine           │ │ ← §6
                        │  │ (Cedar evaluator,       │ │
                        │  │  policy store)          │ │
                        │  └─────────────────────────┘ │
                        │                              │
                        │  Existing:                   │
                        │  - Cloud Relay Service       │
                        │  - A2A Service               │
                        │  - Agent Registry            │
                        └──────────┬───────────────────┘
                                   │ (outbound SSE relay)
                        ┌──────────▼───────────────────┐
                        │       Hybro Hub (Local)      │
                        │                              │
                        │  ┌─────────────────────────┐ │
                        │  │ Key Store               │ │ ← §3
                        │  │ (~/.hybro/keys/)         │ │
                        │  ├─────────────────────────┤ │
                        │  │ Token Cache             │ │ ← §4
                        │  │ (active HCTs)           │ │
                        │  ├─────────────────────────┤ │
                        │  │ OTel Sidecar            │ │ ← §5
                        │  │ (span collection)       │ │
                        │  ├─────────────────────────┤ │
                        │  │ Policy Cache            │ │ ← §6
                        │  │ (offline evaluation)    │ │
                        │  └─────────────────────────┘ │
                        │                              │
                        │  Local Agents               │
                        └──────────────────────────────┘
```

### 7.2 Request Flow with Full Trust Layer

A complete request through the trust layer:

```
1. User sends message in Hybro portal
        │
2. Backend identifies target agent (cloud or local via hub)
        │
3. ┌─ POLICY CHECK (§6) ────────────────────────────────────┐
   │ Cedar evaluates: Can this user's request be routed to  │
   │ this agent, with these scopes, at this time?           │
   │ If DENY → return 403, log policy denial event          │
   └─────────────────────────────────────────────────────────┘
        │ ALLOW
4. ┌─ TOKEN ISSUANCE (§4) ──────────────────────────────────┐
   │ Token Service issues HCT:                              │
   │ - scopes: [financial:read]                             │
   │ - task_id: task-789                                    │
   │ - bound to target agent DID (DPoP)                     │
   │ - delegation_depth: 0 (or more if orchestrator)        │
   └─────────────────────────────────────────────────────────┘
        │
5. ┌─ IDENTITY VERIFICATION (§3) ───────────────────────────┐
   │ Caller verifies target agent's identity:               │
   │ - Resolve DID → get public key                         │
   │ - Verify AgentCard signature or challenge-response     │
   └─────────────────────────────────────────────────────────┘
        │
6. A2A request sent with:
   - Authorization: Bearer <HCT>
   - traceparent: <W3C trace context>
   - tracestate: hybro=room_id:room-abc;task_id:task-789
        │
7. ┌─ TRANSPARENCY (§5) ────────────────────────────────────┐
   │ Events emitted:                                        │
   │ - agent.task.delegated                                 │
   │ - agent.token.issued                                   │
   │ Spans created:                                         │
   │ - trace_id propagated end-to-end                       │
   │ Audit record:                                          │
   │ - hash-chained, signed                                 │
   └─────────────────────────────────────────────────────────┘
        │
8. Target agent processes request, returns response
        │
9. Events: agent.task.completed, span closed
```

### 7.3 Minimal Changes to Existing Backend

The trust layer is designed as a **sidecar** to the existing backend, not a
rewrite:

| Existing Component | Change Required |
|-------------------|----------------|
| `Agent` model | Add optional `identity` field (DID, public key, verification level) |
| `a2a_service.py` | Add HCT validation middleware. Add trace context propagation. |
| `room_center.py` `send_message` | Add policy evaluation before dispatching to agent |
| `ResponseProcessor` | Emit structured events on task completion |
| Cloud Relay | Forward trace context headers to/from hub |
| Frontend `useRoomWebhook.ts` | Display verification badges and data flow indicators |

No existing endpoints change their signatures. No existing data models
lose fields. The trust layer adds new optional fields and new middleware.

---

## 8. Phased Implementation Roadmap

### Phase 0: Foundation (Weeks 1-3)

**Goal:** Core identity and basic token infrastructure.

| Deliverable | Details |
|-------------|---------|
| DID method implementation | `did:hybro` resolver, DID Document generation |
| Key pair generation | ED25519 keys for agents, stored in Agent model |
| AgentCard `identity` extension | Optional identity block in AgentCard |
| Basic HCT | JWT with `hybro:scopes` and `hybro:task_id`, signed by platform key |
| Token issuance endpoint | `POST /api/v1/tokens/issue` |
| Token validation middleware | FastAPI dependency for A2A request handlers |

**Not in Phase 0:** DPoP, delegation chains, Cedar policies, audit log.

**Exit Criteria:** An agent can issue a scoped, time-bound token to another
agent, and the receiving agent can validate it.

### Phase 1: Policy & Transparency (Weeks 4-7)

**Goal:** Organizational control and observability.

| Deliverable | Details |
|-------------|---------|
| Cedar integration | `cedarpy` engine, policy store in MongoDB |
| Policy evaluation middleware | Pre-dispatch policy check in `send_message` |
| Policy management API | CRUD for org-level policies |
| OpenTelemetry integration | Trace context propagation in A2A requests |
| Structured event logging | Event taxonomy, MongoDB time-series storage |
| Verification level enforcement | Policies can require minimum verification level |
| Delegation chain tracking | `hybro:delegation_chain` in HCT |

**Not in Phase 1:** Tamper-evident audit log, Policy UI, DPoP, hub-side policy cache.

**Exit Criteria:** An org admin can create a policy that blocks unverified
agents. All A2A interactions produce trace spans and structured events.

### Phase 2: Hardening & Hub Integration (Weeks 8-12)

**Goal:** Production-grade security and hub integration.

| Deliverable | Details |
|-------------|---------|
| DPoP (Proof of Possession) | RFC 9449 implementation for HCT binding |
| Tamper-evident audit log | Hash-chained, signed audit records |
| Hub key management | `~/.hybro/keys/` keystore, key rotation |
| Hub policy cache | Sync policies via relay, offline evaluation |
| Hub OTel sidecar | Span collection and batch export via relay |
| X.509 certificates | Hybro CA, per-agent leaf certs |
| Policy admin UI | React page for creating/testing policies |

**Exit Criteria:** Local agents have cryptographic identity, tokens are
DPoP-bound, policies evaluate offline, and audit records are tamper-evident.

### Phase 3: Enterprise & Cross-Org (Weeks 13-18)

**Goal:** Multi-organization federation and enterprise features.

| Deliverable | Details |
|-------------|---------|
| Organization-verified identity | Third-party CA integration, enterprise SSO mapping |
| Cross-org trust federation | Mutual trust policies between orgs |
| Task trace viewer UI | Full call-graph visualization in portal |
| Compliance reporting | Exportable audit reports (PDF, CSV) |
| HSM integration | Hardware security module support for key storage |
| Policy simulation tool | "What-if" tool for testing policy impact |
| Rate limiting per scope | Per-scope token usage quotas |

**Exit Criteria:** Two organizations can establish mutual trust policies,
and all cross-org interactions are fully auditable with exportable reports.

---

## 9. Risks & Open Questions

### 9.1 Risks

| Risk | Impact | Likelihood | Mitigation |
|------|--------|:---:|-----------|
| **Latency from policy evaluation** | Every A2A request adds a Cedar evaluation (~1-5ms). In deep chains, this accumulates. | Medium | Cache policy decisions for identical contexts. Cedar evaluates in microseconds for simple policies. |
| **Key management complexity** | Private keys on user devices are vulnerable. Lost keys = lost identity. | High | Key backup to Hybro cloud (encrypted). Key rotation with grace periods. Clear recovery flow. |
| **Cedar learning curve** | Developers unfamiliar with Cedar's policy language may write incorrect policies. | Medium | Pre-built policy templates. Policy testing sandbox. Clear documentation. |
| **Token proliferation** | In a 5-agent chain, each hop issues a new HCT. This creates N tokens per task. | Low | Tokens are short-lived (minutes). Token cache prevents re-issuance for same task. |
| **Backward compatibility** | Existing agents (no trust layer) may be excluded from interactions. | Medium | Trust layer is opt-in. Unverified agents can still participate in unrestricted rooms. Progressive rollout. |
| **Audit log storage growth** | High-volume deployments may generate millions of audit records per day. | Medium | Time-series compression. TTL-based archival. Tiered storage (hot/warm/cold). |
| **DID method centralization** | `did:hybro` is a centralized DID method (Hybro resolves it). This is a philosophical concern for decentralization purists. | Low | Support `did:web` and `did:key` as alternative methods. `did:hybro` is a convenience, not a requirement. |

### 9.2 Open Questions

| # | Question | Options | Recommendation |
|---|----------|---------|---------------|
| 1 | Should HCTs use asymmetric (ED25519) or symmetric (HMAC) signing? | ED25519: verifiable by anyone. HMAC: faster, but requires shared secret. | ED25519 — public verifiability is essential for cross-org trust. |
| 2 | Should the audit log be anchored to a public blockchain? | Yes: maximum tamper evidence. No: simpler, lower cost. | Phase 3 optional — hash-chain + signature is sufficient for most compliance. |
| 3 | How to handle key rotation for long-running agents? | Revocation list (CRL), OCSP-like status check, or DID Document versioning. | DID Document versioning — most aligned with DID spec. |
| 4 | Should policies be version-controlled (git-like history)? | Yes: audit trail for policy changes. No: simpler. | Yes — policy changes are themselves auditable events. Store policy history in MongoDB. |
| 5 | How granular should the scope taxonomy be? | Coarse (10-20 scopes) vs. fine (100+ resource-specific scopes). | Start coarse (Phase 0-1), expand based on real usage patterns (Phase 2+). |
| 6 | Should local agents evaluate policies locally or always via cloud? | Local: works offline. Cloud: always up-to-date. | Hybrid — cache from cloud, evaluate locally. Stale flag in audit log. |
| 7 | Should DPoP be mandatory or optional? | Mandatory: strongest security. Optional: lower friction for development. | Optional in Phase 0-1, mandatory for cross-org in Phase 2+. |

---

## Appendix A: Standards Reference

| Standard | Version | Use in Hybro |
|----------|---------|-------------|
| W3C DID | 1.0 (Rec, 2022) | Agent identity |
| W3C DID Resolution | 1.0 (Draft) | DID Document retrieval |
| ED25519 | RFC 8032 | Key pairs for signing |
| JWT | RFC 7519 | Capability token format |
| DPoP | RFC 9449 | Proof of possession |
| OAuth 2.0 RAR | RFC 9396 | Fine-grained authorization requests |
| W3C Trace Context | 1.0 (Rec, 2020) | Distributed tracing headers |
| OpenTelemetry | 1.x | Span collection and export |
| Cedar | 4.x | Policy language and evaluation |
| X.509 | RFC 5280 | Certificate-based identity |
| SHA-256 | FIPS 180-4 | Hash chaining in audit log |

## Appendix B: Relationship to AAP

The IETF Agent Authorization Profile (AAP) draft proposes extending OAuth
2.0 with agent-specific claims. Hybro's HCT design is **informed by AAP's
goals** but built on mature standards:

| AAP Concept | Hybro Equivalent | Standard Used |
|-------------|-----------------|---------------|
| Agent identity in token | `sub` claim with DID | W3C DID + JWT |
| Task binding | `hybro:task_id` claim | Custom JWT claim |
| Scope narrowing on delegation | `hybro:max_delegation_depth` + `hybro:delegation_chain` | Custom JWT claims |
| Proof of possession | `hybro:binding` with DPoP | RFC 9449 |
| Fine-grained scopes | `hybro:scopes` with resource:action taxonomy | OAuth 2.0 RAR (RFC 9396) pattern |

If AAP matures to RFC status, Hybro can adopt it as a native token format
alongside HCT, with a compatibility layer mapping between the two.

---

*End of document*
