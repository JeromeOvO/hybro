# Hybro Gateway API — Developer Guide

## Overview

The Hybro Gateway API lets external consumers (SDKs, local daemons, third-party apps) discover and communicate with cloud-hosted A2A agents through a single authenticated entry point at `https://api.hybro.ai/api/v1/gateway`.

All requests require an `X-API-Key` header. API keys are provisioned through the Hybro portal.

---

## Authentication

Every request must include:

```
X-API-Key: hba_your_api_key_here
```

| Status | Meaning |
|--------|---------|
| `401` | Missing or invalid API key |
| `403` | Valid key, but no access to the requested agent |
| `429` | Rate limit exceeded (check `Retry-After` header) |

---

## Endpoints

### 1. Discover Agents

```
POST /api/v1/gateway/agents/discover
```

Search for agents matching a natural-language query. Returns results with gateway-masked URLs so the SDK can communicate through the gateway without knowing the real agent URL.

**Request body:**

```json
{
  "query": "legal contract review",
  "limit": 5
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `query` | string | yes | Natural-language search query |
| `limit` | integer | no | Max results (1–100, default: server-configured) |

**Response (200):**

```json
{
  "query": "legal contract review",
  "agents": [
    {
      "agent_id": "abc-123",
      "agent_card": {
        "name": "Legal Contract Reviewer",
        "url": "https://api.hybro.ai/api/v1/gateway/agents/abc-123/message/send",
        "description": "Reviews legal contracts...",
        "version": "1.0.0",
        "skills": [...],
        "capabilities": { "streaming": true }
      },
      "match_score": 0.92
    }
  ],
  "count": 1
}
```

---

### 2. Send Message (Synchronous)

```
POST /api/v1/gateway/agents/{agent_id}/message/send
```

Send a message and wait for the full response. The gateway proxies the request to the real agent using the A2A protocol.

**Request body:**

```json
{
  "message": {
    "role": "user",
    "parts": [{ "kind": "text", "text": "Review this contract clause..." }],
    "messageId": "unique-msg-id",
    "contextId": "conversation-ctx-id"
  }
}
```

**Response (200):** An A2A `SendMessageResponse` — the exact response from the agent.

---

### 3. Stream Message (SSE)

```
POST /api/v1/gateway/agents/{agent_id}/message/stream
```

Send a message and receive a streaming response via Server-Sent Events. If the agent doesn't support streaming, the gateway falls back to a single SSE event containing the synchronous response.

Authentication, access control, and per-agent rate limits are checked **before** the SSE stream starts. If any check fails, the gateway returns a proper HTTP error status (401/403/404/429) — not an SSE event.

**Request body:** Same as `/message/send`.

**Response:** `text/event-stream`

```
data: {"jsonrpc":"2.0","result":{...}}

data: {"jsonrpc":"2.0","result":{...}}

```

If an error occurs **mid-stream** (after the SSE connection is established):

```
data: {"error":"upstream agent disconnected"}

```

---

### 4. Get Agent Card

```
GET /api/v1/gateway/agents/{agent_id}/card
```

Fetch an agent's card with the URL rewritten to point through the gateway.

**Response (200):**

```json
{
  "agent_id": "abc-123",
  "agent_card": {
    "name": "Legal Contract Reviewer",
    "url": "https://api.hybro.ai/api/v1/gateway/agents/abc-123/message/send",
    "description": "...",
    "version": "1.0.0",
    "skills": [...]
  }
}
```

---

## Rate Limits

| Scope | Default | Header |
|-------|---------|--------|
| Per API key | 200 requests/hour | `Retry-After` (seconds) |
| Global | 20,000 requests/hour | `Retry-After` (seconds) |

Per-agent rate limits may also apply (configured per agent by the provider).

---

## Access Control

- **Public agents** (`is_public: true`): accessible to any authenticated user
- **Private agents** (`is_public: false`): accessible only to the agent's provider (owner)
- **Inactive agents**: not accessible (returns 404)

---

## Error Responses

All errors follow a consistent format:

```json
{
  "detail": {
    "error": "error_code",
    "message": "Human-readable description"
  }
}
```

| Status | Error Code | Description |
|--------|------------|-------------|
| 401 | `invalid_key` / `missing_key` | API key issue |
| 403 | `access_denied` | No access to private agent |
| 404 | `agent_not_found` / `no_results` | Agent not found or no discovery results |
| 429 | `rate_limit_exceeded` | Too many requests |
| 502 | `agent_error` | Upstream agent communication failure |

---

## Python SDK Quickstart

Install:

```bash
pip install hybro-sdk
```

Use:

```python
import asyncio
from hybro_sdk import HybroGateway

async def main():
    async with HybroGateway(api_key="hba_...") as gw:
        # Discover
        agents = await gw.discover("data analysis")

        # Send
        result = await gw.send(agents[0].agent_id, "Analyze this dataset...")

        # Stream
        async for event in gw.stream(agents[0].agent_id, "Summarize findings"):
            print(event.data)

asyncio.run(main())
```

See the full [SDK documentation](https://github.com/hybro-ai/hybro-hub#readme) for detailed API reference and error handling.
