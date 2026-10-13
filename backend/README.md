# Hybro Backend

This directory is the canonical backend for the current Hybro repository and is
the backend used by the application, Docker Compose configuration, and CI.

## Run from the monorepo

### Docker Compose

Configuration lives in `~/.hybro/config.json` (secrets in `~/.hybro/auth.json`);
start the stack through the CLI so each service receives validated JSON-derived
projections:

```sh
./scripts/hybro setup           # once: Provider, authentication and models
./scripts/hybro start --build
```

The API is available at <http://localhost:8000> and its health endpoint is
<http://localhost:8000/health>.

### Run the backend directly

Python 3.12+ and MongoDB 4.2+ are required. Terminal projection scheduling uses
MongoDB aggregation update pipelines; Docker Compose currently provides MongoDB
7.0.

```sh
cd backend
uv sync --extra dev
# Point HYBRO_HOME at a directory containing config.json/auth.json (hybro setup).
uv run uvicorn main:app --reload
```

Set `backend.auth_mode` to `mock` in `config.json` for local development without
Clerk credentials. Redis is optional for a single-process local server;
cross-process delivery and locking require it.

### Production upgrade note

Releases that introduce terminal task writer fencing must not be deployed with a
rolling mixed-version writer fleet. Coordinately drain or stop all old backend
writers, deploy the new version to every replica, and only then resume traffic.
An older writer does not apply the MongoDB terminal winner fences and can undo the
new version's guarantees.

## Project layout

- `main.py`: FastAPI application and lifespan entry point.
- `container.py`: runtime composition root.
- `api_gateway/`: the only HTTP route package.
- `agent/`, `room/`, `execution/`, `context_memory/`, `delivery/`: domain modules.
- `a2a_adapter/`: A2A SDK boundary.
- `llm_gateway/`: LLM provider boundary.
- `local_agents/`: Docker-host A2A Agent discovery and lifecycle coordination.
- `dal/`: MongoDB and Redis adapters.
- `room_files/`: room-owned file metadata and local content storage.
- `tests/`: unit, boundary, and workflow tests.

The former `api/` compatibility package has been removed. New routes belong in
`api_gateway/routes/` and must use injected owner protocols rather than concrete
repositories.

See [`docs/System-Architecture.md`](docs/System-Architecture.md) for the current
runtime architecture.

## Agent-network API

These endpoints let an authenticated client discover visible registered agents
and send one A2A message without creating a room. The frontend's **Connect > API**
(`/connect/api`) page provides copyable request examples; existing room chat continues to use its
authenticated REST/SSE workflow.

**Security boundary:** both routes use the existing `get_current_user` dependency.
With `backend.auth_mode="clerk"`, callers need a valid Clerk session; add
`-H "Authorization: Bearer <Clerk session JWT>"` to the curl examples below.
With local `backend.auth_mode="mock"`, the existing override supplies
`user_local_developer`; this mode must remain on a trusted network.
Discovery and sending are limited to public agents and the caller's own private
agents. Saved groups must belong to that caller. Discovery removes legacy
`authentication.credentials`, not arbitrary secrets embedded in Card metadata.

### Discover stored active agents

```sh
curl --fail-with-body --silent --show-error \
  'http://localhost:8000/api/v1/agents/discovery'

# Optional scope: use an existing saved group/Team ID from the frontend.
curl --fail-with-body --silent --show-error --get \
  --data-urlencode 'group_id=my-saved-team-id' \
  'http://localhost:8000/api/v1/agents/discovery'
```

The response shape is `{"agents":[{"agent_id":"...","agent_card":{...}}]}`.
Each `agent_card` is the stored raw Card, except that legacy
`authentication.credentials` is stripped. There is no live scan, Card refresh,
health probe, search ranking, or fixed inventory cap.

- Omit `group_id`, or use `all_agents`, for all visible stored active agents:
  public agents plus the caller's own private agents.
- A saved group/Team must belong to the caller and returns only its currently
  active, visible registered members. An empty group or one with no visible
  active members returns `{"agents":[]}`.
- Another owner's group returns HTTP `403`.
- An unknown group returns HTTP `404`.
- `room_team` returns HTTP `422`: this built-in selection requires room context
  and is not supported here.

### Choose an agent

Three read-only routes help a caller pick an agent without loading every Card.
They use the same authentication, visibility, and saved-group ownership rules
as discovery.

```sh
# Saved groups (subnets) owned by the caller, with active visible member counts.
curl --fail-with-body --silent --show-error \
  'http://localhost:8000/api/v1/agents/groups'

# Rank agents for a capability query, optionally within one saved group.
curl --fail-with-body --silent --show-error --get \
  --data-urlencode 'query=weather forecast' \
  --data-urlencode 'group_id=my-saved-team-id' \
  --data-urlencode 'limit=5' \
  'http://localhost:8000/api/v1/agents/search'

# One agent's consumer profile.
curl --fail-with-body --silent --show-error \
  'http://localhost:8000/api/v1/agents/registered-agent-id/profile'
```

- `GET /agents/groups` returns `{"groups":[{"group_id","name","description","agent_count"}]}`.
  Built-in `all_agents` and `room_team` selections are not listed.
- `GET /agents/search` requires `query` (1-500 characters) and accepts
  `limit` (1-20, default 5) and `group_id` with the same scope rules as
  discovery. Ranking is lexical over Card names, descriptions, skill names,
  tags, and descriptions, so short capability keywords work best. Each match
  has `agent_id`, `name`, `description`, compact `skills`, `input_modes`,
  `output_modes`, the caller's `groups` containing the agent, and `score`.
  An empty list means no agent matched the query terms.
- `GET /agents/{agent_id}/profile` returns `status`, `name`, `description`,
  `version`, `provider`, `streaming`, modes, skills with examples and modes,
  and `groups`. It leaves out URLs, transport interfaces, and security
  details because Hybro makes the call. Missing or invisible agents return `404`.

### Send one message

Replace `registered-agent-id` with an `agent_id` from discovery; the ID belongs
in the request body, not in the URL. This example assumes an agent that can
multiply two numbers:

```sh
curl --fail-with-body --silent --show-error \
  'http://localhost:8000/api/v1/agents/messages' \
  -H 'Content-Type: application/json' \
  --data '{
    "agent_id": "registered-agent-id",
    "client_request_id": "multiply-17-23-1",
    "message": {
      "role": "user",
      "messageId": "multiply-message-1",
      "parts": [{"kind": "text", "text": "Multiply 17 by 23."}]
    }
  }'
```

Add `"group_id":"my-saved-team-id"` to the outer object to enforce saved-group
membership and ownership at send time. Without it (or with `all_agents`), any
visible registered active agent may be targeted. The service reloads visibility,
status, and group ownership rather than trusting an earlier discovery response.

`client_request_id` and `message.messageId` are generated when omitted; the
message role defaults to `user`, the only accepted caller role. `parts` must be
nonempty. Request text parts use the existing common A2A shape with `kind:"text"`;
supported data and file parts use their corresponding common-model shapes.
Room file IDs alone are not accepted as file content.

An illustrative Task response is:

```json
{
  "agent_id": "registered-agent-id",
  "client_request_id": "multiply-17-23-1",
  "result": {
    "kind": "task",
    "id": "remote-task-1",
    "contextId": "remote-context-1",
    "status": {
      "state": "TASK_STATE_COMPLETED",
      "message": {
        "messageId": "remote-reply-1",
        "role": "ROLE_AGENT",
        "parts": [{"text": "PRODUCT=391"}]
      }
    }
  }
}
```

The remote agent can instead return a Message, for example:

```json
{
  "agent_id": "registered-agent-id",
  "client_request_id": "multiply-17-23-1",
  "result": {
    "kind": "message",
    "messageId": "remote-reply-1",
    "role": "ROLE_AGENT",
    "parts": [{"text": "PRODUCT=391"}]
  }
}
```

These are shape examples, not a promise about any agent's output. `result`
preserves the SDK's A2A 1.0 ProtoJSON fields plus the adapter's top-level `kind`
discriminator: enums are `ROLE_AGENT` / `TASK_STATE_COMPLETED`, and result text
parts have `text` **without** legacy `kind:"text"`. Do not deserialize results
as the legacy request model or expect a flattened text answer. Task history,
artifacts, metadata, status messages, and continuation IDs remain in the result.
A nonterminal or failed Task also returns HTTP `200`; inspect its state.

### Continuation and failure semantics

To continue an agent conversation, keep its returned `contextId`. To continue a
specific Task, also put its returned `id` into the next `message.taskId`. Send a
new user message to the same agent with a new `messageId`, for example:

```json
{
  "agent_id": "registered-agent-id",
  "client_request_id": "follow-up-2",
  "message": {
    "messageId": "follow-up-message-2",
    "contextId": "remote-context-1",
    "taskId": "remote-task-awaiting-input",
    "parts": [{"kind": "text", "text": "Use whole numbers."}]
  }
}
```

Use IDs actually returned by that agent. Omit `taskId` for a new task in a reusable
context; do not assume a completed Task can be reopened. The remote agent owns
continuation validity. This API does not bind task/context IDs to a
caller, store conversation history, or provide room/HITL/LLM orchestration.

The call has a **600-second deadline** and performs one blocking send, without
automatic retries or Docker-host fallback. A timeout stops the local wait,
**not the remote task**: no A2A cancellation is sent, and the agent may continue
working after HTTP `504` or client disconnection. `client_request_id` is only a
correlation ID, not an idempotency key; resubmitting the same request can repeat
remote effects. There are no polling, cancellation, or SSE endpoints for this
network API.

`POST /agents/messages` accepts at most **6 MiB** of total request-body bytes,
including JSON and base64 file content. The limit also applies without
`Content-Length`. Each backend worker admits at most **four concurrent requests**,
including requests still receiving their body. Additional requests receive HTTP
`429` immediately; there is no waiting queue. Slots are released when the request
exits, including errors and cancellation. These limits are per worker, not a
distributed quota; remote work may still outlive a timed-out request.

Errors use HTTP `401` for missing/invalid authentication, `403` for another owner's
group, `404` for a missing group or missing/invisible agent, `409` for an inactive
or out-of-group visible agent, `413` for an oversized body, `422` for invalid input
or `room_team`, `429` for exhausted concurrent admission, `502` for upstream
transport/protocol or invalid-result failures, and `504` for timeout.
Domain errors have `detail.code` and `detail.message`; domain send errors also
include `detail.agent_id` and `detail.client_request_id`. Authentication and
middleware errors use a string `detail`; schema validation errors use FastAPI's
validation-detail array.

Implementation ownership and protocol inventory are documented in
[`System Architecture`](docs/System-Architecture.md#agent-network-api).

## Local MCP access

The repository-root [`MCP/`](../MCP/) directory contains a separate Python
Streamable HTTP adapter for these two APIs. It does not import the backend
runtime, access the database, create rooms, or change Agent visibility rules.

`hybro start` and the TUI's **Start services** start MCP as an ordinary default
Compose service. Its own `MCP/Dockerfile` builds the adapter; released stacks use
the version-pinned `hybro-mcp` image. Host access is published only on
`127.0.0.1:8001:8001`. Use a trusted backend in `backend.auth_mode="mock"`.

For independent control, use `hybro mcp start|stop|status|logs` or **Services >
MCP connection** in the TUI. Standalone start requires setup and an already
running backend: it runs `up -d --no-deps mcp`, without starting dependencies.
The TUI shows status, URL, and transport, with start, stop, logs, and refresh
actions. Exiting it leaves MCP running; stopping MCP does not exclude it from
the next global start. `hybro mcp status` exits nonzero unless ready.

Add a **Streamable HTTP** server manually to your MCP client using:

```text
http://127.0.0.1:8001/mcp
```

This URL is for clients on the machine running Hybro, not a remote browser's
machine. **Connect** (`/connect/mcp`) in the app copies the URL and per-assistant
setup snippets, such as the Claude Code `mcpServers` configuration
(`type: "http"`); the snippets can also be selected and copied manually. It does
not check status, control services, relay tool calls, or automatically
discover/register clients. The Overview **Gateway** tile shows MCP status.

Native manual startup remains supported from the repository root, with a backend
already running at `http://127.0.0.1:8000/api/v1/`:

```sh
uv run --project MCP --frozen --no-env-file python MCP/server.py
```

The adapter has its own locked environment and exposes five tools. Backend
saved groups appear as **subnets** (`subnet_id`) in tool arguments and results.

- `list_subnets()`: the caller's subnets with active member counts, from
  `GET /agents/groups`.
- `find_agents(query, subnet_id?, limit?)`: ranked compact matches from
  `GET /agents/search`, each listing its subnets.
- `get_agent(agent_id)`: one agent's consumer profile from
  `GET /agents/{agent_id}/profile`.
- `discover_agents(subnet_id?)`: every active agent with its raw Card, from
  `GET /agents/discovery`. Output is large; prefer `find_agents` to choose.
- `send_message(agent_id, text, conversation?, data?, files?, subnet_id?,
  client_request_id?, include_raw?)`: builds the A2A message, sends it through
  `POST /agents/messages`, and returns a summary.

For example, after `find_agents`, call `send_message` with:

```json
{
  "agent_id": "<an ID returned by find_agents>",
  "text": "Write a short story about a robot seeing the sea."
}
```

The summary is:

```json
{
  "agent_id": "...",
  "client_request_id": "...",
  "state": "input_required",
  "needs_input": true,
  "reply_text": "How long should it be?",
  "data": [],
  "files": [],
  "conversation": {"context_id": "remote-context-1", "task_id": "remote-task-1"},
  "next_action": "Answer the agent's question, ..."
}
```

`state` is the A2A task state in lowercase without the `TASK_STATE_` prefix;
a direct Message reply is `completed`. `reply_text` joins text from the status
message or Message and from artifacts, skipping status text that an artifact
already contains; `data` and `files` collect the other
parts, and inline file bytes are omitted. To continue, pass `conversation` back
unchanged. It carries `task_id` only while the Task is `submitted`, `working`,
`input_required`, or `auth_required`, so a finished Task is never reopened.
`include_raw: true` adds the complete A2A result as `raw`. Files accept `uri`
(preferred) or small base64 `bytes`.

Every tool publishes an output schema. Results are sent as both MCP structured
content and JSON text. Failed, rejected, canceled, or expired Tasks and all
HTTP/transport errors set `isError`. Errors keep the backend `detail` and add a
`next_action` hint, for example that `429` was not accepted and can be retried
shortly, while `504` may still be running and must not be resent. A missing
`client_request_id` is generated before sending so local transport failures can
also be correlated.

The adapter allows 610 seconds for the backend's 600-second deadline and response
delivery. Configure the MCP client's tool timeout to exceed 610 seconds if it
supports that setting. There are no automatic retries, remote cancellation, or
polling.

Native mode uses fixed loopback endpoints and the `/api/v1` prefix. Container
mode (`--container`) requires the CLI's validated `HYBRO_MCP_CONFIG` projection,
containing only `api_prefix`; it connects to `backend:8000` and listens on
`0.0.0.0:8001` inside Docker. Explicit Host/Origin DNS-rebinding checks remain
active in both modes. MCP receives no auth store or Provider credentials.

`GET http://127.0.0.1:8001/health` makes a read-only backend `agents/discovery`
request with a two-second deadline and returns only `service: "hybro-mcp"` and
`status: "ready" | "unavailable" | "unsupported_auth"`. It never executes
agents or exposes Cards. A rejected authentication probe (`401`/`403`) reports
`unsupported_auth`; Compose marks the container unhealthy if it cannot report
ready, including with Clerk. The public frontend `GET /hybro-mcp` route exposes
only this sanitized status, not backend data or credentials.

The adapter has **no MCP authentication or user isolation** and supplies no
Clerk credentials: Clerk-mode tool requests fail rather than bypassing auth.
Use an authenticated REST client for Clerk-mode access; do not disable production
authentication to use this adapter. Do not expose the adapter publicly. The
adapter only calls the public agent-network API; Execution ownership is unchanged.

To run the isolated regression tests from the repository root:

```sh
uv run --project MCP --frozen --no-env-file pytest MCP/tests
```

The `MCP CI (local adapter)` job runs this same frozen, dotenv-disabled test command.

## Validation

Run from `backend/`:

```sh
uv run ruff format --check .
uv run ruff check .
uv run pytest -m core
uv run pytest
```

Real Redis integration tests are explicit:

```sh
HYBRO_TEST_REDIS_URL=redis://localhost:6379/0 uv run pytest -m integration
```

See [`tests/README.md`](tests/README.md) for test lanes and cleanup conventions.

## A2A inline file limits

`A2A_INLINE_FILE_MAX_RAW_BYTES` limits one uploaded file before base64 encoding.
`A2A_INLINE_MESSAGE_MAX_ENCODED_BYTES` limits aggregate encoded file bytes in an
outbound A2A message. Uploaded files sent to agents use inline A2A bytes; local
filesystem paths and authenticated room-file URLs remain internal to Hybro.
