# Backend Observability

Hybro has one process-wide logging pipeline owned by
`common.observability`. Application modules use Python's standard
`logging.Logger`; modules must not add handlers or write log files.

## Configuration

- `LOG_LEVEL`: `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`.
- `LOG_FORMAT`: `auto`, `json`, or `logfmt`.
- `auto` selects logfmt in `development` and JSON in every other environment.
- All application and Uvicorn error logs go to stdout.
- Uvicorn access logging is disabled. `RequestLoggingMiddleware` emits the
  single `http_request_completed` event for each HTTP request.
- `httpx`, MongoDB, Redis, and their low-level clients default to `WARNING`.

`LOG_PATH` and rotation settings are no longer runtime configuration. Existing
local files under `logs/` are intentionally left untouched.

## Event Contract

Every formatted event contains:

- `timestamp`, `level`, `service`, `environment`, `version`
- process-wide eight-character `instance_id`
- stable snake-case `event`
- originating `logger`

Structured `extra` mappings are flattened with dotted keys. Correlation fields
are inherited through `ContextVar`:

`trace_id`, `request_id`, `client_request_id`, `room_id`, `run_id`,
`user_message_id`, `message_id`, `turn_id`, `agent_id`, `task_id`, and
`dispatch_intent_id`.

`request_id` identifies only the current HTTP request. `trace_id` is copied to
background tasks, public delivery envelopes, and generic internal-event
envelopes and is restored by cross-instance consumers. A valid caller-provided `X-Request-ID` is reused; otherwise the
gateway generates a UUID and returns it as `X-Request-ID`.

Terminal run and delivery events include `outcome` and `duration_ms`. The main
execution events are:

- `gateway_send_message_received`
- `supervisor_run_started`
- `supervisor_planner_completed`
- `agent_call_completed`
- `a2a_call_completed`
- `llm_call_completed`
- `supervisor_run_completed`
- `delivery_completed`
- `http_request_completed`

Cancellation persistence/recovery emits
`cancellation_finalization_pending` when a durable marker is accepted but its
synchronous finalization fails, and `cancellation_marker_reconciliation_failed`
when one pending marker fails during a recovery page. Both carry bounded
message/room identifiers through structured fields and exception metadata; no
message content is logged. A repository page-scan failure propagates to the
stale-task checker cycle so the existing `stale_task_checker_failed` event makes
the failed sweep observable instead of reporting a partial success.

Internal eventing exposes health independently as
`app.state.eventing_connected`; it is not folded into
`delivery_pubsub_connected`. The bounded event bus retains eventing-owned dead
letters for `queue_full`, `handler`, `fanout`, and `deserialization` failures and
best-effort publishes the same structured record on the independent
`eventing:dead_letter` channel. Dead letters include origin, event type, trace ID,
failure stage, exception class plus a redacted message byte-size/SHA-256/
fingerprint summary, timestamp, and bounded scalar metadata. Raw exception text
is never retained or published. Event bodies are never retained or published:
the payload field is a
redacted projection containing only byte size, SHA-256, bounded top-level key
names, and allow-listed identifiers (room/run/message/task/agent/hub/journal and
idempotency/correlation IDs). Invalid raw Redis messages receive the same
size/hash-only treatment. Each serialized dead letter is capped at 8 KiB.
Shutdown handler timeouts use failure stage `shutdown_handler_timeout` so a
handler that ignores task cancellation is observable without blocking shutdown.

`agent_call_completed` is owned by Supervisor dispatch and represents the
logical agent operation. `a2a_call_completed` is owned by the A2A client facade
and represents one transport-level request. Keeping these events distinct
prevents a single dispatch from being counted twice.

## Privacy and Bounds

Logging formatters redact credentials, cookies, headers, prompts, bodies,
payloads, content, and base64/bytes fields. URLs lose user-info, query strings,
and fragments. Raw byte values are represented only by length. Individual
fields are capped at 2 KiB and exception stacks at 16 KiB.

Do not log prompts, user or agent response text, artifact contents, complete
payloads, headers, or complete URLs even at DEBUG. Record counts, booleans,
identifiers, provider/model names, status, error type/code, and latency instead.
Exceptions retain `error_type`, `error_chain`, a stable `error_fingerprint`,
and a compact file/function/line `error_stack`; exception messages, arguments,
locals, and source lines are excluded. Legacy parameterized log arguments are
also rendered conservatively so they cannot bypass structured-field redaction.

## Usage

```python
from common.observability import bind_log_context, get_logger, traced_create_task

logger = get_logger(__name__)

with bind_log_context(room_id=room_id, run_id=run_id):
    logger.info(
        "worker_completed",
        extra={"outcome": "success", "duration_ms": duration_ms},
    )
    task = traced_create_task(run_worker(), name=f"worker-{run_id}")
```

`configure_logging(settings)` is called once by the application shell before
runtime startup. It is idempotent for tests and embedding, but business modules
must not call it.
