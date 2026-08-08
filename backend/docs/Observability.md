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

Internal eventing exposes health independently as
`app.state.eventing_connected`; it is not folded into
`delivery_pubsub_connected`. The bounded event bus retains eventing-owned dead
letters for `queue_full`, `handler`, `fanout`, and `deserialization` failures and
best-effort publishes the same structured record on the independent
`eventing:dead_letter` channel. Dead letters include origin, event type, trace ID, failure stage,
exception class/message, timestamp, and bounded metadata. Payloads must still
follow the privacy rules below.

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
