# SSE Delivery Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the backend room SSE core path to the final Delivery design: typed backend `DeliveryEvent` DTOs translated by Delivery into one frontend wire format, with legacy raw SSE and legacy routing fallbacks removed.

**Architecture:** Backend modules emit typed domain delivery events; `delivery.translator` is the only place that turns those DTOs into frontend SSE frames. Frontend wire format is always `{ "type": string, "timestamp": string, "room_id": string, "data": object }`; backend-internal `event_type` is never sent to the browser as the outer protocol.

**Tech Stack:** Python 3.11+, FastAPI `StreamingResponse`, Pydantic DTOs, asyncio, pytest.

---

## Final Contract

Frontend already expects this wire contract:

```ts
type SSEFrame<T extends string, D> = {
  type: T
  timestamp: string
  room_id: string
  data: D
}
```

Backend cleanup must enforce these invariants:

- Every room SSE frame has top-level `type`, `timestamp`, `room_id`, and `data`.
- `connected` has `data: { connection_id: string }`.
- `heartbeat` has `data: {}`.
- `data.timestamp` is removed from final room SSE frames; top-level `timestamp` is authoritative.
- `processing_status.details` is `dict | None`, never a string.
- `processing_status.status` accepts `queued`, `processing`, `awaiting_input`, `completed`, `failed`, `canceled`, `rejected`, `rate_limited`, and `error`.
- Production room SSE emission does not call `emit_legacy_frame()` or raw `broadcast_to_room()`.

## File Structure

- Modify `common/dto/delivery.py`: widen `ProcessingStatusEvent`; add typed DTOs for task, artifact, error, and richer HITL events.
- Modify `delivery/translator.py`: translate all typed DTOs into final `SSEFrame`; remove nested `data.timestamp`.
- Modify `delivery/sse/connection.py`: return final heartbeat frame.
- Modify `app_shell/delivery_runtime.py`: update the compatibility `SSEConnection` heartbeat while it still exists.
- Modify `api_gateway/routes/sse_routes.py`: return final connected frame.
- Modify `delivery/facade.py`: expose typed `emit(event: DeliveryEvent)` for app-shell adapters.
- Modify `app_shell/delivery_runtime.py`: keep connection/cancellation helpers, but make `send_*` helpers emit typed DTOs; remove raw frame construction.
- Modify `execution/events.py`: remove legacy processing-status branch; emit widened typed `ProcessingStatusEvent`.
- Modify `execution/ports.py`: remove `LegacyProcessingStatusPublisher` once no callers need it; keep `ClientRequestIdResolver`.
- Create `execution/client_request_id.py`: new home for `SSEClientRequestIdResolver` before deleting the legacy processing-status module.
- Modify `main.py`: stop constructing/injecting `LegacyProcessingStatusC3Adapter`.
- Modify `execution/hitl/adapters.py` and `execution/hitl/service.py`: emit typed HITL DTOs.
- Modify `execution/hitl/factory.py`: inject typed HITL delivery instead of `_sse_manager`.
- Modify `execution/run_lifecycle_service.py`: remove `broadcast_run_event_payload()` raw broadcaster path or leave as a test-only compatibility helper with no production callers.
- Modify `api_gateway/routes/room_routes.py`: remove `target_group` fallback.
- Modify `api_gateway/routes/room_routes.py`: delete deprecated `createAndParseUserMessage` 410 route.
- Modify `app_shell/room_runtime.py`: stop letting canonical `mentioned_agent_ids` bypass supervisor; pass explicit mention intent into supervisor/LLM context.
- Delete `execution/legacy_processing_status.py` after all imports are gone.
- Update tests under `tests/test_delivery_translator.py`, `tests/test_delivery_sse_connection.py`, `tests/test_api_sse.py`, `tests/test_delivery_event_publisher.py`, `tests/test_sse_adapter_delivery.py`, `tests/test_phase7_execution_event_gate.py`, `tests/test_service_hitl.py`, `tests/test_scope_validation.py`, and `tests/test_api_room_center.py`.

---

### Task 1: Lock Final SSE Frame Translation

**Files:**
- Modify: `common/dto/delivery.py`
- Modify: `common/a2a_constants.py`
- Modify: `delivery/translator.py`
- Modify: `delivery/sse/connection.py`
- Modify: `app_shell/delivery_runtime.py`
- Modify: `api_gateway/routes/sse_routes.py`
- Test: `tests/test_delivery_translator.py`
- Test: `tests/test_common_a2a_constants.py`
- Test: `tests/test_delivery_sse_connection.py`
- Test: `tests/test_service_sse.py`
- Test: `tests/test_api_sse.py`

- [ ] **Step 1: Write failing translator tests for final processing status frames**

Replace `tests/test_delivery_translator.py::test_processing_status_translation` expected output with no nested `data.timestamp`, and add a test for all final statuses:

```python
def test_processing_status_translation_uses_final_frame_without_nested_timestamp():
    event = ProcessingStatusEvent(
        room_id="room-1",
        message_id="msg-1",
        status="awaiting_input",
        details={"reason": "hitl"},
        agent_id="agent-1",
        client_request_id="cr-1",
        agents=[{"agent_id": "agent-1"}],
        trace_id="trace-1",
    )

    assert to_sse_frame(event, timestamp=NOW) == {
        "type": "processing_status",
        "timestamp": NOW.isoformat(),
        "room_id": "room-1",
        "data": {
            "status": "awaiting_input",
            "message_id": "msg-1",
            "details": {"reason": "hitl"},
            "agent_id": "agent-1",
            "client_request_id": "cr-1",
            "agents": [{"agent_id": "agent-1"}],
            "trace_id": "trace-1",
        },
    }


def test_processing_status_accepts_all_final_statuses():
    statuses = [
        "queued",
        "processing",
        "awaiting_input",
        "completed",
        "failed",
        "canceled",
        "rejected",
        "rate_limited",
        "error",
    ]

    for status in statuses:
        event = ProcessingStatusEvent(
            room_id="room-1",
            message_id="msg-1",
            status=status,
            details=None,
        )
        frame = to_sse_frame(event, timestamp=NOW)
        assert frame["type"] == "processing_status"
        assert frame["data"]["status"] == status
        assert "timestamp" not in frame["data"]
```

- [ ] **Step 2: Write failing connection tests for final heartbeat**

Update `tests/test_delivery_sse_connection.py::test_timeout_returns_heartbeat_frame`:

```python
assert frame == {
    "type": "heartbeat",
    "timestamp": NOW.isoformat(),
    "room_id": "room-1",
    "data": {},
}
```

Update `tests/test_delivery_sse_connection.py::test_get_message_serializes_heartbeat_on_timeout` to expect the same `data: {}` shape after `json.loads(raw)`.

Also update the compatibility connection test in `tests/test_service_sse.py::TestSSEConnection::test_get_message_returns_heartbeat_on_timeout`:

```python
assert parsed == {
    "type": "heartbeat",
    "timestamp": parsed["timestamp"],
    "room_id": "room-1",
    "data": {},
}
assert isinstance(parsed["timestamp"], str)
```

- [ ] **Step 3: Write failing SSE route test for final connected frame**

In `tests/test_api_sse.py`, add or update the stream test so the first yielded event parses as:

```python
assert json.loads(first_event.removeprefix("data: ").strip()) == {
    "type": "connected",
    "room_id": "room-1",
    "timestamp": ANY_STRING,
    "data": {"connection_id": ANY_STRING},
}
```

Use the existing test helpers in `tests/test_api_sse.py`; if there is no `ANY_STRING`, assert key presence and value types.

- [ ] **Step 4: Update all translator golden expectations for no nested timestamps**

In `tests/test_delivery_translator.py`, update every existing expected frame to remove `timestamp` from `frame["data"]`. This includes:

- `test_agent_message_partial_translation`
- `test_agent_message_final_translation_merges_content`
- `test_cancellation_translation`
- `test_hitl_request_translation`
- `test_hitl_resolved_translation`
- `test_hub_agent_event_translation`
- `test_debate_round_translation`

Each expected frame should keep only the top-level `"timestamp": NOW.isoformat()`.

Also update `test_hitl_resolved_translation` so its `HITLResolvedEvent(...)` constructor includes `source="agent"`. Task 2 makes `source` required on `HITLResolvedEvent`; the existing golden test must construct the final DTO shape instead of relying on an implicit default.

- [ ] **Step 5: Add reserved-key test for `AgentMessageFinal.content`**

In `tests/test_delivery_translator.py`, add:

```python
def test_agent_message_final_translation_drops_reserved_timestamp_from_content():
    event = AgentMessageFinal(
        room_id="room-1",
        message_id="msg-1",
        agent_id="agent-1",
        content={"content": "done", "timestamp": "nested"},
    )

    frame = to_sse_frame(event, timestamp=NOW)

    assert frame["timestamp"] == NOW.isoformat()
    assert "timestamp" not in frame["data"]
```

- [ ] **Step 6: Run the failing tests**

Run:

```bash
pytest tests/test_delivery_translator.py tests/test_delivery_sse_connection.py tests/test_api_sse.py -q
```

Expected: failures showing old nested timestamps, unsupported statuses, or missing `data` on connected/heartbeat.

- [ ] **Step 7: Widen shared processing status constants and DTO**

In `common/a2a_constants.py`, modify the existing `SSEProcessingStatus` enum by adding `QUEUED = "queued"` above `PROCESSING`; do not create a second enum class:

```python
class SSEProcessingStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    CANCELED = "canceled"
    FAILED = "failed"
    REJECTED = "rejected"
    RATE_LIMITED = "rate_limited"
    ERROR = "error"
    AWAITING_INPUT = "awaiting_input"
```

Do not add `QUEUED` to `PROCESSING_DONE_STATUSES`; it is non-terminal.

In `tests/test_common_a2a_constants.py`, update `test_processing_status_values_are_shared_strings()`:

```python
assert SSEProcessingStatus.QUEUED.value == "queued"
assert SSEProcessingStatus.QUEUED not in PROCESSING_DONE_STATUSES
```

Import `PROCESSING_DONE_STATUSES` in that test file.

In `common/dto/delivery.py`, change `ProcessingStatusEvent.status` to:

```python
status: Literal[
    "queued",
    "processing",
    "awaiting_input",
    "completed",
    "failed",
    "canceled",
    "rejected",
    "rate_limited",
    "error",
]
```

Keep `details: dict | None = None`.

- [ ] **Step 8: Remove nested timestamps from `delivery/translator.py`**

For every branch in `to_sse_frame()`, remove `"timestamp": frame_timestamp` from the `data` dict. Keep the top-level timestamp created by `_frame()`.

For `AgentMessageFinal`, do not blindly allow `event.content` to overwrite reserved frame fields. Merge only non-reserved content keys:

```python
reserved_data_keys = {"timestamp"}
data.update({
    key: value
    for key, value in event.content.items()
    if key not in reserved_data_keys
})
```

- [ ] **Step 9: Add final heartbeat shape**

In `delivery/sse/connection.py::next_frame()`, return:

```python
return {
    "type": "heartbeat",
    "timestamp": self._now().isoformat(),
    "room_id": self.room_id,
    "data": {},
}
```

In the compatibility `app_shell/delivery_runtime.py::SSEConnection.get_message()` timeout branch, return the same final heartbeat shape when serializing the JSON heartbeat:

```python
return json.dumps(
    {
        "type": "heartbeat",
        "timestamp": utcnow().isoformat(),
        "room_id": self.room_id,
        "data": {},
    }
)
```

- [ ] **Step 10: Add final connected shape**

In `api_gateway/routes/sse_routes.py`, change `connected_message` to:

```python
connected_message = {
    "type": "connected",
    "room_id": room_id,
    "timestamp": utcnow().isoformat(),
    "data": {
        "connection_id": connection.connection_id,
    },
}
```

- [ ] **Step 11: Run translation and transport tests**

Run:

```bash
pytest tests/test_delivery_translator.py tests/test_common_a2a_constants.py tests/test_delivery_sse_connection.py tests/test_service_sse.py tests/test_api_sse.py -q
```

Expected: PASS.

---

### Task 2: Add Typed DTOs for Existing Raw Event Shapes

**Files:**
- Modify: `common/dto/delivery.py`
- Modify: `common/dto/__init__.py`
- Modify: `delivery/translator.py`
- Test: `tests/test_delivery_translator.py`
- Test: `tests/test_common_foundation.py`

- [ ] **Step 1: Write failing translator tests for typed task/artifact/error/HITL DTOs**

Add tests in `tests/test_delivery_translator.py`:

```python
def test_task_submitted_translation():
    event = TaskSubmittedEvent(
        room_id="room-1",
        message_id="agent-msg-1",
        task_id="task-1",
        agent_name="Agent",
        agent_id="agent-1",
        status="working",
        related_message_id="user-msg-1",
        created_at="created",
        step_number=1,
        total_steps=2,
        task_content="do work",
        client_request_id="cr-1",
    )

    assert to_sse_frame(event, timestamp=NOW) == {
        "type": "task_submitted",
        "timestamp": NOW.isoformat(),
        "room_id": "room-1",
        "data": {
            "message_id": "agent-msg-1",
            "task_id": "task-1",
            "agent_name": "Agent",
            "agent_id": "agent-1",
            "status": "working",
            "related_message_id": "user-msg-1",
            "created_at": "created",
            "step_number": 1,
            "total_steps": 2,
            "task_content": "do work",
            "client_request_id": "cr-1",
        },
    }


def test_task_update_translation():
    event = TaskUpdateEvent(
        room_id="room-1",
        message_id="agent-msg-1",
        status="input-required",
        content="content",
        error=None,
        requires_input=True,
        requires_auth=False,
        status_message="waiting",
        agent_name="Agent",
        agent_id="agent-1",
        related_message_id="user-msg-1",
        created_at="created",
        step_number=1,
        total_steps=2,
        task_content="do work",
        parts=[{"kind": "text"}],
        client_request_id="cr-1",
    )

    frame = to_sse_frame(event, timestamp=NOW)
    assert frame["type"] == "task_update"
    assert frame["data"]["message_id"] == "agent-msg-1"
    assert frame["data"]["requires_input"] is True
    assert frame["data"]["created_at"] == "created"
    assert frame["data"]["parts"] == [{"kind": "text"}]
    assert "timestamp" not in frame["data"]


def test_artifact_update_translation():
    event = ArtifactUpdateEvent(
        room_id="room-1",
        message_id="agent-msg-1",
        agent_id="agent-1",
        artifact={"kind": "file"},
        append=True,
        last_chunk=False,
        client_request_id="cr-1",
    )

    assert to_sse_frame(event, timestamp=NOW)["data"] == {
        "message_id": "agent-msg-1",
        "agent_id": "agent-1",
        "artifact": {"kind": "file"},
        "append": True,
        "last_chunk": False,
        "client_request_id": "cr-1",
    }


def test_error_event_translation():
    event = ErrorEvent(
        room_id="room-1",
        error="slow down",
        error_type="rate_limit_exceeded",
        message_id="msg-1",
        agent_id="agent-1",
        retry_after_seconds=5,
        client_request_id="cr-1",
    )

    frame = to_sse_frame(event, timestamp=NOW)
    assert frame["type"] == "error"
    assert frame["data"]["error_type"] == "rate_limit_exceeded"


def test_hitl_request_translation_preserves_full_payload():
    event = HITLRequestEvent(
        room_id="room-1",
        request_id="hitl-1",
        message_id="msg-1",
        source="agent",
        prompt="Pick one",
        prompt_type="choice",
        choices=["a", "b"],
        agent_id="agent-1",
        agent_name="Agent",
        source_step_id="step-1",
        group_id="group-1",
        group_total=2,
        group_index=1,
        client_request_id="cr-1",
    )

    frame = to_sse_frame(event, timestamp=NOW)

    assert frame == {
        "type": "hitl_input_requested",
        "timestamp": NOW.isoformat(),
        "room_id": "room-1",
        "data": {
            "request_id": "hitl-1",
            "message_id": "msg-1",
            "source": "agent",
            "prompt": "Pick one",
            "prompt_type": "choice",
            "choices": ["a", "b"],
            "agent_id": "agent-1",
            "agent_name": "Agent",
            "source_step_id": "step-1",
            "group_id": "group-1",
            "group_total": 2,
            "group_index": 1,
            "client_request_id": "cr-1",
        },
    }


def test_hitl_status_translation_preserves_status_source_and_error():
    event = HITLResolvedEvent(
        room_id="room-1",
        request_id="hitl-1",
        message_id="msg-1",
        source="agent",
        status="error",
        error_message="expired",
        client_request_id="cr-1",
    )

    frame = to_sse_frame(event, timestamp=NOW)

    assert frame == {
        "type": "hitl_status_update",
        "timestamp": NOW.isoformat(),
        "room_id": "room-1",
        "data": {
            "request_id": "hitl-1",
            "message_id": "msg-1",
            "source": "agent",
            "status": "error",
            "error_message": "expired",
            "client_request_id": "cr-1",
        },
    }
```

- [ ] **Step 2: Run the failing translator tests**

Run:

```bash
pytest tests/test_delivery_translator.py -q
```

Expected: FAIL because `TaskSubmittedEvent`, `TaskUpdateEvent`, `ArtifactUpdateEvent`, and `ErrorEvent` are not defined, and the current HITL translator drops the widened HITL payload fields.

- [ ] **Step 3: Add DTO classes**

In `common/dto/delivery.py`, add:

```python
class TaskSubmittedEvent(DeliveryEventBase):
    event_type: Literal["task_submitted"] = "task_submitted"
    message_id: str
    task_id: str
    agent_name: str
    agent_id: str | None = None
    status: str = "working"
    related_message_id: str | None = None
    created_at: str | None = None
    step_number: int | None = None
    total_steps: int | None = None
    task_content: str | None = None
    client_request_id: str | None = None


class TaskUpdateEvent(DeliveryEventBase):
    event_type: Literal["task_update"] = "task_update"
    message_id: str
    status: str
    content: str | None = None
    error: str | None = None
    requires_input: bool = False
    requires_auth: bool = False
    status_message: str | None = None
    agent_name: str | None = None
    agent_id: str | None = None
    related_message_id: str | None = None
    created_at: str | None = None
    step_number: int | None = None
    total_steps: int | None = None
    task_content: str | None = None
    parts: list[dict] | None = None
    client_request_id: str | None = None


class ArtifactUpdateEvent(DeliveryEventBase):
    event_type: Literal["artifact_update"] = "artifact_update"
    message_id: str
    agent_id: str
    artifact: Any
    append: bool = False
    last_chunk: bool = False
    client_request_id: str | None = None


class ErrorEvent(DeliveryEventBase):
    event_type: Literal["error"] = "error"
    error: str
    error_type: str | None = None
    message_id: str | None = None
    agent_id: str | None = None
    retry_after_seconds: int | None = None
    user_requests_used: int | None = None
    user_requests_limit: int | None = None
    system_requests_used: int | None = None
    system_requests_limit: int | None = None
    client_request_id: str | None = None
```

Add those classes to the `DeliveryEvent` union and `__all__`. Export them from `common/dto/__init__.py`.

- [ ] **Step 4: Widen HITL DTOs to match production payload**

In `common/dto/delivery.py`, extend:

```python
class HITLRequestEvent(DeliveryEventBase):
    event_type: Literal["hitl_request"] = "hitl_request"
    request_id: str
    message_id: str
    source: str
    prompt: str
    prompt_type: str
    choices: list[str] | None = None
    agent_id: str | None = None
    agent_name: str | None = None
    source_step_id: str | None = None
    group_id: str | None = None
    group_total: int | None = None
    group_index: int | None = None
    client_request_id: str | None = None


class HITLResolvedEvent(DeliveryEventBase):
    event_type: Literal["hitl_resolved"] = "hitl_resolved"
    request_id: str
    message_id: str
    source: str
    status: str = "resolved"
    error_message: str | None = None
    client_request_id: str | None = None
```

- [ ] **Step 5: Add translator branches**

In `delivery/translator.py`, import the new DTOs and translate them to `type` values:

```python
"task_submitted"
"task_update"
"artifact_update"
"error"
```

Use `_add_optional()` for nullable optional fields and do not include nested `timestamp`.

Also update existing HITL translation:

- `HITLRequestEvent` must include `choices`, `agent_id`, `agent_name`, `source_step_id`, `group_id`, `group_total`, `group_index`, and `client_request_id` when present.
- `HITLResolvedEvent` must use `event.status` instead of hard-coding `"resolved"`, and must include `source`, `error_message`, and `client_request_id` when present.

- [ ] **Step 6: Update common foundation schema tests**

In `tests/test_common_foundation.py`, update imports and exact schema assertions:

- Add `TaskSubmittedEvent`, `TaskUpdateEvent`, `ArtifactUpdateEvent`, and `ErrorEvent` to the DTO imports.
- Add exact `model_fields` entries for the new DTOs.
- Add `created_at` to `TaskSubmittedEvent` and `TaskUpdateEvent` expected field sets.
- Add widened HITL fields to `HITLRequestEvent` and `HITLResolvedEvent` expected field sets.
- Update `expected_required_fields` for new DTOs and widened HITL DTOs.

- [ ] **Step 7: Run DTO and translator tests**

Run:

```bash
pytest tests/test_delivery_translator.py tests/test_common_foundation.py -q
```

Expected: PASS.

---

### Task 3: Remove Legacy Processing Status Compatibility Path

**Files:**
- Create: `execution/client_request_id.py`
- Modify: `execution/events.py`
- Modify: `execution/ports.py`
- Modify: `execution/facade.py`
- Modify: `execution/orchestration/queue_executor.py`
- Modify: `execution/orchestration/supervisor_executor.py`
- Modify: `execution/orchestration/room_message_center.py`
- Modify: `execution/dispatch/task_notifications.py`
- Modify: `execution/dispatch/response_handler.py`
- Modify: `execution/run_lifecycle.py`
- Modify: `main.py`
- Modify: `tests/fixtures/phase7_execution_event_callers.json`
- Delete: `execution/legacy_processing_status.py`
- Test: `tests/test_phase7_execution_event_gate.py`
- Test: `tests/test_common_a2a_constants.py`
- Test: `tests/test_agent_response_handler.py`
- Test: `tests/test_execution_facade.py`
- Test: `tests/test_supervisor_improvements.py`

- [ ] **Step 1: Write failing tests for all typed statuses and object-only details**

In `tests/test_phase7_execution_event_gate.py`, add:

```python
@pytest.mark.asyncio
async def test_emit_processing_status_uses_typed_event_for_all_final_statuses():
    publisher = AsyncMock()
    run_lifecycle = AsyncMock()
    run_lifecycle.record_processing_status.return_value = None
    resolver = AsyncMock()
    resolver.resolve_client_request_id.return_value = "cr-1"

    for status in [
        "queued",
        "processing",
        "awaiting_input",
        "completed",
        "failed",
        "canceled",
        "rejected",
        "rate_limited",
        "error",
    ]:
        await emit_processing_status(
            room_id="room-1",
            status=status,
            message_id="msg-1",
            run_lifecycle=run_lifecycle,
            event_publisher=publisher,
            run_event_enabled=lambda: False,
            client_request_id_resolver=resolver,
            details={"status": status},
        )

    emitted = [call.args[0] for call in publisher.emit.await_args_list]
    assert [event.status for event in emitted] == [
        "queued",
        "processing",
        "awaiting_input",
        "completed",
        "failed",
        "canceled",
        "rejected",
        "rate_limited",
        "error",
    ]
```

Also add a validation test that passing `legacy_details="text"` raises `TypeError` or remove the parameter from the function signature and update call sites.

- [ ] **Step 2: Run the failing event tests**

Run:

```bash
pytest tests/test_phase7_execution_event_gate.py -q
```

Expected: FAIL because `emit_processing_status()` still requires `legacy_processing_status_publisher` and still routes unsupported statuses through legacy compatibility frames.

- [ ] **Step 3: Convert production string `details` callers before deleting `legacy_details`**

Before removing the `legacy_details` parameter, update every production caller that currently passes human-readable string details so the final SSE payload remains object-only:

```python
details={"message": "Failed to parse user message"}
```

Use `error_message="..."` only when the text is specifically a failure/cancel reason that must also be recorded as lifecycle error text.

At minimum, inspect and update the following current string-detail sites and wrapper splitters:

- `app_shell/room_runtime.py`
- `execution/orchestration/queue_executor.py`
- `execution/orchestration/supervisor_executor.py`
- `execution/orchestration/room_message_center.py`
- `execution/dispatch/task_notifications.py`
- `execution/dispatch/response_handler.py`
- `execution/run_lifecycle.py`
- `jobs/stale_task_checker.py`
- `main.py`

Known non-literal string-producing sites that must be converted include:

- `app_shell/room_runtime.py`: `details=selection_result.error or "Agent selection failed"`
- `execution/orchestration/supervisor_executor.py`: `details=f"Delegating to {len(action.targets)} agent(s)..."`
- `app_shell/relay_service.py`: `details=payload.get("details")`
- `execution/facade.py`: `_agent_event_details(_thaw_hub_payload_value(payload.get("details")))`

In `main.py`, update `emit_watchdog_processing_status()` so it no longer passes `legacy_details=details`. Convert the helper to pass:

```python
details={"message": details} if details else None
```

or:

```python
error_message=details
```

when the watchdog event represents a failure that should be recorded as lifecycle error text.

Run:

```bash
rg -n "details\\s*=|legacy_details|error_message=legacy_details" app_shell execution jobs main.py -S \
  -g '!execution/run_lifecycle_service.py'
```

Expected after this step: every remaining `details=` hit in production, excluding `execution/run_lifecycle_service.py` which is migrated in Task 5, passes a dict expression, `None`, or a variable whose type is normalized to `dict | None` immediately before the call. `execution/run_lifecycle.py` should pass lifecycle error text through an `error_message` variable, not a frontend `details` value. No remaining hit should pass a raw string expression, f-string, `payload.get("details")`, or `legacy_details`. `legacy_details` hits inside `execution/events.py` are removed later in Step 5.

- [ ] **Step 4: Move the client request id resolver out of the legacy module**

Create `execution/client_request_id.py`:

```python
from __future__ import annotations


class SSEClientRequestIdResolver:
    def __init__(self, db_service) -> None:
        self._db_service = db_service

    async def resolve_client_request_id(
        self,
        message_id: str | None,
        provided_client_request_id: str | None,
    ) -> str | None:
        if provided_client_request_id:
            return provided_client_request_id
        if not message_id:
            return None
        return await self._db_service.resolve_client_request_id_for_message_id(
            message_id
        )
```

Update imports in `main.py` and tests from:

```python
from execution.legacy_processing_status import SSEClientRequestIdResolver
```

to:

```python
from execution.client_request_id import SSEClientRequestIdResolver
```

- [ ] **Step 5: Simplify `execution/events.py`**

Change `SUPPORTED_TYPED_PROCESSING_STATUSES` to include all final statuses. Remove:

```python
_is_legacy_processing_status()
_legacy_processing_status_details()
_requires_legacy_processing_status_frame()
legacy_processing_status_publisher parameter
legacy_details parameter
uses_legacy_frame branch
```

Keep `error_message` mapping into `details={"message": error_message}`.

Update `tests/test_phase7_execution_event_gate.py` at the same time:

- Import `SSEClientRequestIdResolver` from `execution.client_request_id`.
- Delete tests for `LegacyProcessingStatusC3Adapter`.
- Delete tests for `_is_legacy_processing_status()`.
- Replace unsupported-status compatibility assertions with typed-event assertions for `awaiting_input`, `rejected`, `rate_limited`, and `error`.
- Remove fixture text that says `"typed or legacy compatibility frame"` and replace it with `"typed DeliveryEvent frame"`.

- [ ] **Step 6: Update `execution/facade.py` call sites**

Remove `legacy_processing_status_publisher=self._legacy_processing_status_publisher` from every `emit_processing_status()` call.

Remove the `_legacy_processing_status_publisher` constructor field if no longer used.

- [ ] **Step 7: Update direct legacy processing-status adapter users**

Current production code imports or receives `LegacyProcessingStatusC3Adapter` outside `ExecutionFacade`. Replace those dependencies with direct typed `emit_processing_status()` wiring:

- `execution/orchestration/queue_executor.py`
- `execution/orchestration/supervisor_executor.py`
- `execution/orchestration/room_message_center.py`
- `execution/dispatch/task_notifications.py`
- `execution/dispatch/response_handler.py`

For each file, remove `legacy_processing_status_publisher` parameters and pass the local `run_lifecycle`, `event_publisher`, `run_event_enabled`, and `client_request_id_resolver` dependencies into the central `execution.events.emit_processing_status()` helper. If a class already receives these dependencies through `bind_execution_event_deps()` or constructor wiring, reuse that local dependency holder instead of introducing a new service locator.

- [ ] **Step 8: Update `main.py` dependency assembly**

Remove import and construction of `LegacyProcessingStatusC3Adapter`. Remove the argument passed into `ExecutionFacade`.

Update object construction for `QueueExecutor`, `SupervisorExecutor`, `RoomMessageCenter`, task notification helpers, and response handlers so none of them receive `legacy_processing_status_publisher`.

- [ ] **Step 9: Remove legacy port and file**

In `execution/ports.py`, delete `LegacyProcessingStatusPublisher`.

Delete `execution/legacy_processing_status.py` only after these checks pass:

```bash
rg -n "from execution\\.legacy_processing_status|import execution\\.legacy_processing_status|LegacyProcessingStatus" execution main.py tests -S
```

Expected before deleting the file: the only hit may be the class definition inside `execution/legacy_processing_status.py`. No imports or external references may remain. Delete `execution/legacy_processing_status.py`, then rerun the command and expect no hits.

```bash
rg -n "SSEClientRequestIdResolver" execution main.py tests -S
```

Expected: hits are allowed only when importing from `execution.client_request_id` or defining the class in `execution/client_request_id.py`.

- [ ] **Step 10: Run processing status tests**

Run:

```bash
pytest \
  tests/test_phase7_execution_event_gate.py \
  tests/test_common_a2a_constants.py \
  tests/test_agent_response_handler.py \
  tests/test_execution_facade.py \
  tests/test_supervisor_improvements.py \
  tests/test_module_queue_executor.py \
  tests/test_module_room_message_center.py \
  tests/test_service_task_notification.py \
  -q
```

Expected: PASS.

---

### Task 4: Make AppShell SSE Helpers Emit Typed Events

**Files:**
- Modify: `delivery/facade.py`
- Modify: `app_shell/delivery_runtime.py`
- Modify: `tests/delivery_adapter_fakes.py`
- Modify: `tests/test_sse_adapter_delivery.py`
- Modify: `tests/test_delivery_event_publisher.py`

- [ ] **Step 1: Update delivery adapter fakes for typed emit**

In `tests/delivery_adapter_fakes.py`, add `FakeEventPublisher` before `FakeDeliveryFacade`:

```python
class FakeEventPublisher:
    def __init__(self):
        self.events = []

    async def emit(self, event):
        self.events.append(event)
```

Then update `FakeDeliveryFacade` so `AppShellSSEManager` can call `self._facade.emit(event)`:

```python
class FakeDeliveryFacade:
    def __init__(
        self,
        *,
        compat: FakeDeliveryCompat | None = None,
        instance_id: str = "test-worker",
        event_publisher: FakeEventPublisher | None = None,
    ) -> None:
        self.compat = compat or FakeDeliveryCompat()
        self.instance_id = instance_id
        self.event_publisher = event_publisher or FakeEventPublisher()

    async def emit(self, event) -> None:
        await self.event_publisher.emit(event)
```

Update `make_bound_manager()` to accept and pass through `event_publisher`.

- [ ] **Step 2: Write failing adapter tests that assert typed emit is used**

In `tests/test_sse_adapter_delivery.py`, replace raw-frame assertions with event assertions. Import `FakeEventPublisher` from `tests.delivery_adapter_fakes`.

Then assert:

```python
await manager.send_task_update("room-1", "msg-1", "working", client_request_id="cr-1")

assert len(fake_publisher.events) == 1
event = fake_publisher.events[0]
assert event.event_type == "task_update"
assert event.room_id == "room-1"
assert event.message_id == "msg-1"
assert event.status == "working"
assert event.client_request_id == "cr-1"
```

- [ ] **Step 3: Run failing adapter tests**

Run:

```bash
pytest tests/test_sse_adapter_delivery.py -q
```

Expected: FAIL because `AppShellSSEManager` still builds raw frames and calls `compat.emit_legacy_frame()`.

- [ ] **Step 4: Expose typed emit through `DeliveryFacade`**

In `delivery/facade.py`, add:

```python
async def emit(self, event: DeliveryEvent) -> None:
    await self._event_publisher.emit(event)
```

Import `DeliveryEvent` from `common.dto`.

- [ ] **Step 5: Update `AppShellSSEManager` facade protocol**

In `app_shell/delivery_runtime.py`, update `_DeliveryFacadeLike` to include:

```python
event_publisher: Any
async def emit(self, event: DeliveryEvent) -> None:
    pass
```

Import the typed DTOs from `common.dto`.

- [ ] **Step 6: Replace `send_*` helper internals with typed events**

In `app_shell/delivery_runtime.py`, make these methods construct DTOs and call `await self._facade.emit(event)`:

- `send_agent_response()` -> `AgentMessageFinal`
- `send_error()` -> `ErrorEvent`
- `send_rate_limit_error()` -> `ErrorEvent(error_type="rate_limit_exceeded")`
- `send_artifact_update()` -> `ArtifactUpdateEvent`
- `send_processing_status()` -> `ProcessingStatusEvent`
- `send_task_submitted()` -> `TaskSubmittedEvent`
- `send_task_update()` -> `TaskUpdateEvent`

When mapping task DTOs, preserve the existing optional `created_at` field from both `send_task_submitted()` and `send_task_update()`; callers in `app_shell/room_coordinator_service.py` and `execution/dispatch/transports/direct.py` already pass it.

When mapping `send_agent_response()` to `AgentMessageFinal`, preserve the current wire fields by packing them into `AgentMessageFinal.content`:

```python
content_payload = {
    "content": content,
    "related_message_id": related_message_id,
}
if client_request_id:
    content_payload["client_request_id"] = client_request_id
if parts:
    content_payload["parts"] = parts
event = AgentMessageFinal(
    room_id=room_id,
    message_id=message_id,
    agent_id=agent_id,
    content=content_payload,
)
```

Keep `send_user_message()` only if production still uses it; otherwise delete it with its tests.

- [ ] **Step 7: Keep `broadcast_to_room()` temporarily but stop using it from `send_*` wrappers**

Do not delete `AppShellSSEManager.broadcast_to_room()` yet. Task 5 still migrates current direct raw callers in `execution/run_lifecycle_service.py`, `execution/hitl/adapters.py`, and `execution/hitl/service.py`.

After this step, `send_agent_response()`, `send_error()`, `send_rate_limit_error()`, `send_artifact_update()`, `send_processing_status()`, `send_task_submitted()`, and `send_task_update()` must not call `broadcast_to_room()` internally.

- [ ] **Step 8: Run adapter and publisher tests**

Run:

```bash
pytest tests/test_sse_adapter_delivery.py tests/test_delivery_event_publisher.py -q
```

Expected: PASS.

---

### Task 5: Migrate Remaining Raw Broadcast Callers

**Files:**
- Modify: `execution/ports.py`
- Modify: `execution/run_lifecycle_service.py`
- Modify: `execution/hitl/adapters.py`
- Modify: `execution/hitl/factory.py`
- Modify: `execution/hitl/service.py`
- Modify: `main.py`
- Modify: `app_shell/room_runtime.py`
- Modify: `jobs/stale_task_checker.py`
- Modify: `tests/fixtures/phase7a_processing_status_callers.json`
- Test: `tests/test_sse_adapter_delivery.py`
- Test: `tests/test_run_lifecycle_service.py`
- Test: `tests/test_service_hitl.py`
- Test: `tests/test_stale_task_checker_run_lifecycle.py`
- Test: `tests/test_phase7a_processing_status_gate.py`

- [ ] **Step 1: Find remaining production raw broadcasts**

Run:

```bash
rg -n "broadcast_to_room\\(|emit_legacy_frame\\(|_emit_legacy_frame\\(" app_shell execution delivery jobs api_gateway main.py -S
```

Expected before implementation: hits in `app_shell/delivery_runtime.py`, `delivery/facade.py`, `delivery/event_publisher.py`, `execution/run_lifecycle_service.py`, `execution/hitl/adapters.py`, `execution/hitl/service.py`, and any remaining orchestration/dispatch files still using the old adapter. `execution/hitl/factory.py` is changed for dependency wiring but should not appear in this raw-broadcast scan.

- [ ] **Step 2: Update run lifecycle SSE emission**

Remove production use of:

```python
await sse.broadcast_to_room(
    room_id,
    "run_event",
    build_run_event_sse_payload(payload, client_request_id=client_request_id),
)
```

Use the already-existing `run_event_notification_from_payload()` plus `event_publisher.emit()` path from `execution/events.py`.

If `record_and_maybe_broadcast_run_event()` remains, change its signature to accept `event_publisher: EventPublisher` instead of `sse: RunEventBroadcaster`.

Update `tests/test_phase7a_processing_status_gate.py` so ordering assertions no longer require the old helper names `record_and_maybe_broadcast_run_event()` / `broadcast_run_event_payload()`. The test should assert the behavior instead: lifecycle recording happens before `RunEventNotification`, and `RunEventNotification` happens before `ProcessingStatusEvent` when run-event SSE is enabled.

Update `tests/fixtures/phase7a_processing_status_callers.json` so entries no longer document delivery through `self.sse_manager` or legacy run-event broadcast helpers. The fixture should describe the new typed event path and keep `requires_recording` / ordering metadata aligned with the rewritten gate.

Update `tests/test_run_lifecycle_service.py` so tests no longer pass `sse=FakeSSE()` or assert raw `broadcast_to_room()` calls. Replace those assertions with a fake `EventPublisher` that records `RunEventNotification` objects, and assert that `event_publisher.emit()` receives the typed event after lifecycle recording.

Normalize `execution/run_lifecycle_service.py` `details` handling so any public helper accepts `details: dict[str, Any] | None` or converts string details into `{"message": text}` before calling lifecycle or delivery code. After this step, `rg -n "details\\s*=" execution/run_lifecycle_service.py -S` should show only dict/null-safe uses.

- [ ] **Step 3: Update HITL delivery port**

In `execution/ports.py`, change `HITLDeliveryPort` to:

```python
from common.dto import HITLRequestEvent, HITLResolvedEvent


class HITLDeliveryPort(Protocol):
    async def emit(self, event: HITLRequestEvent | HITLResolvedEvent) -> None:
        pass
```

Use this one-method shape so the HITL service does not need to branch on transport method names.

- [ ] **Step 4: Replace `LegacyHITLDeliveryAdapter`**

In `execution/hitl/adapters.py`, replace `LegacyHITLDeliveryAdapter` with:

```python
class HITLDeliveryAdapter:
    def __init__(self, event_publisher) -> None:
        self._event_publisher = event_publisher

    async def emit(self, event) -> None:
        await self._event_publisher.emit(event)
```

- [ ] **Step 5: Wire HITL service to the typed delivery port**

In `execution/hitl/factory.py`, replace the dependency attr mapping:

```python
"sse_manager": "_sse_manager",
```

with:

```python
"delivery": "_delivery",
```

In `execution/hitl/service.py`, rename the dependency from `self.sse_manager` / `_sse_manager` to `self.delivery` / `_delivery`, and call:

```python
await self.delivery.emit(event)
```

In `main.py`, change the `create_hitl_service` call from:

```python
sse_manager=sse_manager,
```

to:

```python
delivery=HITLDeliveryAdapter(delivery_facade.event_publisher),
```

Use the actual assembled `delivery_facade` or `event_publisher` variable already present in `main.py`; do not create a second publisher.

- [ ] **Step 6: Build typed HITL events in `execution/hitl/service.py`**

Replace `_emit_hitl_event()` raw `message_type` logic with construction of:

- `HITLRequestEvent` for `HITLEventType.INPUT_REQUESTED`
- `HITLResolvedEvent` for `INPUT_RECEIVED`, `INPUT_EXPIRED`, `INPUT_CANCELED`, and `ERROR`

Preserve existing fields:

```python
request_id
message_id
source
client_request_id
prompt
prompt_type
choices
agent_id
agent_name
source_step_id
group_id
group_total
group_index
status
error_message
```

- [ ] **Step 7: Document wrapper-handled helper callers**

Do not rewrite every caller of `sse_manager.send_error()`, `send_task_submitted()`, `send_task_update()`, or `send_artifact_update()` if the wrapper itself now emits typed DTOs. Representative callers such as `hub_runtime_bridge/adapters/legacy_failure.py` and `execution/dispatch/transports/relay.py` are intentionally handled through the typed wrapper path.

Add a regression test in `tests/test_sse_adapter_delivery.py` that calls `send_error()` and `send_task_submitted()` through `AppShellSSEManager` and asserts the fake event publisher receives `ErrorEvent` and `TaskSubmittedEvent`.

- [ ] **Step 8: Run HITL and lifecycle tests**

Run:

```bash
pytest tests/test_run_lifecycle_service.py tests/test_service_hitl.py tests/test_stale_task_checker_run_lifecycle.py tests/test_phase7a_processing_status_gate.py -q
```

Expected: PASS.

- [ ] **Step 9: Verify direct raw broadcast callers are gone**

Run:

```bash
rg -n "broadcast_to_room\\(|emit_legacy_frame\\(|_emit_legacy_frame\\(" app_shell execution delivery jobs api_gateway main.py -S
```

Expected at the end of Task 5: no business call sites remain in `execution`, `jobs`, `api_gateway`, or `main.py`. Temporary method definitions in `app_shell/delivery_runtime.py`, `delivery/facade.py`, and `delivery/event_publisher.py` are allowed until Task 7 deletes the legacy surface.

---

### Task 6: Clean Message Target and Mention Routing Legacy

**Files:**
- Modify: `api_gateway/routes/room_routes.py`
- Modify: `common/dto/execution.py`
- Modify: `app_shell/bound.py`
- Modify: `app_shell/room_runtime.py`
- Modify: `app_shell/openai_service.py`
- Modify: `models/supervisor.py`
- Modify: `execution/orchestration/room_supervisor_service.py`
- Modify: `openapi.json`
- Modify: `tests/fixtures/api_gateway_route_inventory_before.json`
- Modify: `tests/fixtures/api_gateway_route_inventory_expected.json`
- Modify: `tests/fixtures/phase9_api_routes.json`
- Test: `tests/test_api_room_center.py`
- Test: `tests/test_scope_validation.py`
- Test: `tests/test_service_room.py`
- Delete or replace: `tests/test_create_and_parse.py`
- Test: `tests/test_flow_contracts.py`
- Test: `tests/test_api_gateway_route_inventory.py`
- Test: `tests/test_api_thin_adapters.py`
- Test: `tests/test_api_gateway_module_boundaries.py`

- [ ] **Step 1: Write failing route tests for target contract**

In `tests/test_api_room_center.py`, add:

```python
@pytest.mark.asyncio
async def test_send_message_rejects_missing_message_target_mode_without_mentions(
    monkeypatch,
    background_tasks,
    clerk_user,
):
    payload = {
        "room_id": "room-1",
        "message": {"message_content": {"message_text": "hello"}},
        "client_request_id": "cr-1",
    }
    request = FakeRequest(payload)
    monkeypatch.setattr(
        "api_gateway.routes.room_routes.verify_room_ownership",
        AsyncMock(return_value=None),
    )

    response = await send_message(request, background_tasks, clerk_user)

    assert response.status_code == 400
    assert "message_target_mode is required" in response.error
```

Also add:

```python
@pytest.mark.asyncio
async def test_send_message_rejects_legacy_target_group(
    monkeypatch,
    background_tasks,
    clerk_user,
):
    payload = {
        "room_id": "room-1",
        "message": {"message_content": {"message_text": "hello"}},
        "client_request_id": "cr-1",
        "target_group": "room_team",
    }
    request = FakeRequest(payload)
    monkeypatch.setattr(
        "api_gateway.routes.room_routes.verify_room_ownership",
        AsyncMock(return_value=None),
    )

    response = await send_message(request, background_tasks, clerk_user)

    assert response.status_code == 400
    assert "target_group is no longer supported" in response.error
```

If `tests/test_api_room_center.py` uses a differently named request/user fixture, reuse the existing local fixture names and keep the same payload and assertions.

Update existing send-message tests that currently still send legacy payloads:

- In `tests/test_api_room_center.py`, replace valid-request payloads that use `target_group` with `message_target_mode` and `target_group_id`. For tests that intentionally exercise legacy input, assert the 400 response from `test_send_message_rejects_legacy_target_group`.
- In `tests/test_flow_contracts.py`, update non-owner or valid send-message contract payloads so they include a valid `message_target_mode`. Any contract case that keeps `target_group` must expect rejection instead of success.

- [ ] **Step 2: Remove `target_group` fallback in route**

In `api_gateway/routes/room_routes.py`, replace:

```python
else:
    target_group = request_data.get("target_group", "room_team")
```

with explicit validation:

```python
elif mentioned_agent_ids:
    target_group = "room_team"
else:
    return RoomCenterUserMessageResponse(
        message_id=None,
        message=None,
        success=False,
        error="message_target_mode is required when mentioned_agent_ids is not provided",
        status_code=400,
    )
```

Before that branch, reject `target_group` if present:

```python
if "target_group" in request_data:
    return RoomCenterUserMessageResponse(
        message_id=None,
        message=None,
        success=False,
        error="target_group is no longer supported; use message_target_mode and target_group_id",
        status_code=400,
    )
```

- [ ] **Step 3: Write failing supervisor mention test**

In `tests/test_scope_validation.py` or a new focused test file, add a test where room `extend_info.use_supervisor` is true and request has `mentioned_agent_ids=["agent-1"]`. Assert `_handle_mentions_flow()` is not called and `_prepare_for_supervisor()` receives explicit mentions.

Use monkeypatch/AsyncMock around `RoomServices._handle_mentions_flow` and `_prepare_for_supervisor`:

```python
handle_mentions = AsyncMock()
prepare_supervisor = AsyncMock(return_value=ParseResult(success=True))
monkeypatch.setattr(room_center, "_handle_mentions_flow", handle_mentions)
monkeypatch.setattr(room_center, "_prepare_for_supervisor", prepare_supervisor)

await room_center.send_message_to_room(request, target_group="room_team", mentioned_agent_ids=["agent-1"])

handle_mentions.assert_not_awaited()
assert prepare_supervisor.await_args.kwargs["explicit_mentions"] == [
    {
        "agent_id": "agent-1",
        "agent_name": "Agent One",
        "mention_text": "<@agent-1|Agent One>",
    }
]
```

- [ ] **Step 4: Pass explicit mentions into supervisor instead of bypassing it**

In `app_shell/room_runtime.py`:

1. Keep `_validate_canonical_mentions()` pre-persist validation.
2. For `use_supervisor` and `pre_resolved_mentions`, do not call `_handle_mentions_flow()`.
3. Before entering the normal supervisor preparation branch, derive supervisor scope from `pre_resolved_mentions`:

```python
if use_supervisor and pre_resolved_mentions:
    selected_agent_set = {
        mention["agent_id"]: mention["agent_name"]
        for mention in pre_resolved_mentions
    }
    agents = [
        await self.database_service.get_agent_by_agent_id(mention["agent_id"])
        for mention in pre_resolved_mentions
    ]
    agents = [agent for agent in agents if agent is not None]
    auto_assign = False
```

This prevents the later `selected_agent_set` / `agents` branch from falling through to an empty scope.

4. Pass `explicit_mentions=pre_resolved_mentions` into `_prepare_for_supervisor()`; keep the canonical `mention_text` field returned by `_validate_canonical_mentions()`.
5. Store it in `user_message.extend_info["explicit_mentions"]`.
6. Include it in supervisor context/prompt input.

Change `_prepare_for_supervisor()` signature to:

```python
async def _prepare_for_supervisor(
    self,
    room: Room,
    user_message: RoomUserMessage,
    message_text: str,
    agents: list | None,
    selected_agent_set: dict,
    is_debate_mode: bool,
    room_memory: "RoomMemory | None",
    token: CancellationToken | None = None,
    explicit_mentions: list[dict] | None = None,
) -> ParseResult:
```

Add into `user_message.extend_info`:

```python
"explicit_mentions": explicit_mentions or [],
```

- [ ] **Step 5: Make non-supervisor mention behavior explicit**

For `use_supervisor is False`, keep deterministic `_handle_mentions_flow()` for canonical `mentioned_agent_ids`. This preserves current fast-path behavior: explicit mention means hard route to those agents.

Document this in a short comment above the branch:

```python
# In non-supervisor rooms, explicit mentions are hard routing. In supervisor
# rooms, mentions are strong intent signals for the supervisor planner.
```

- [ ] **Step 6: Update LLM parse prompt for explicit mention intent**

In `models/supervisor.py`, extend `RoomConfig`:

```python
class RoomConfig(BaseModel):
    is_debate_mode: bool = False
    room_agent_set: dict[str, str] = Field(default_factory=dict)
    explicit_mentions: list[dict] = Field(default_factory=list)
```

In `app_shell/room_runtime.py::_prepare_for_supervisor()`, pass `explicit_mentions` into `RoomConfig` and persist it in `extend_info`.

In `execution/orchestration/room_supervisor_service.py`, add an explicit mention section to the supervisor prompt. The prompt must tell the supervisor which agents were explicitly mentioned and that these mentions are strong routing intent.

Use wording equivalent to:

```text
## Explicit Mentions
The user explicitly mentioned these agents:
{explicit_mentions}

Treat explicit mentions as strong routing intent. Use the mentioned agents unless
they are unavailable, unsafe, or clearly irrelevant. You may add other agents only
if the task requires it. If you do not use a mentioned agent, explain why.
```

Update the code that formats `SUPERVISOR_SYSTEM_PROMPT` so it supplies a stable `explicit_mentions` string from `RoomConfig.explicit_mentions`.

In `app_shell/openai_service.py`, update the non-supervisor user-message parse prompt path so explicit mentions can be included as structured context when used outside supervisor LLM parsing.

Use wording equivalent to:

```text
The user explicitly mentioned these agents. Treat them as strong routing intent.
Use them unless unavailable, unsafe, or clearly irrelevant. You may add other
agents only if the task requires it.
```

- [ ] **Step 7: Delete deprecated `createAndParseUserMessage` route and service surface**

In `api_gateway/routes/room_routes.py`, delete the `@router.post("/roomCenter/createAndParseUserMessage", deprecated=True)` route and its `create_and_parse_user_message` function.

Delete the corresponding app-shell service/protocol methods:

- `app_shell/room_runtime.py::RoomServices.create_and_parse_user_message`
- `app_shell/room_runtime.py::RoomRuntimeFacade.create_and_parse_user_message`
- `app_shell/bound.py` protocol entry for `create_and_parse_user_message`

Remove tests that exercise this old service path:

- Delete `tests/test_create_and_parse.py`, or replace it with coverage for `/roomCenter/sendMessage` that does not contain `createAndParseUserMessage` / `create_and_parse_user_message` in the file.
- `tests/test_api_room_center.py::TestCreateAndParseUserMessage`
- `tests/conftest.py` mock attributes for `create_and_parse_user_message`
- `tests/test_api_gateway_module_boundaries.py` allowlist/expectations for `create_and_parse_user_message`

Update route inventory fixtures to remove `/api/v1/roomCenter/createAndParseUserMessage`:

- `tests/fixtures/api_gateway_route_inventory_before.json`
- `tests/fixtures/api_gateway_route_inventory_expected.json`
- `tests/fixtures/phase9_api_routes.json`

Regenerate or edit `openapi.json` so `/api/v1/roomCenter/createAndParseUserMessage` is removed from the generated API schema.

After this step, run:

```bash
rg -n "createAndParseUserMessage|create_and_parse_user_message" api_gateway app_shell tests openapi.json -S
```

Expected: no hits.

- [ ] **Step 8: Run route and room tests**

Run:

```bash
pytest \
  tests/test_api_room_center.py \
  tests/test_scope_validation.py \
  tests/test_service_room.py \
  tests/test_flow_contracts.py \
  tests/test_api_gateway_route_inventory.py \
  tests/test_api_thin_adapters.py \
  tests/test_api_gateway_module_boundaries.py \
  -q
```

Expected: PASS.

---

### Task 7: Delete Legacy Raw Frame Surface and Add Regression Gates

**Files:**
- Modify: `delivery/event_publisher.py`
- Modify: `delivery/facade.py`
- Modify: `app_shell/delivery_runtime.py`
- Modify: `tests/test_delivery_event_publisher.py`
- Modify: `tests/test_sse_adapter_delivery.py`
- Modify: `tests/test_delivery_protocols.py`
- Modify: `tests/test_service_sse.py`
- Modify: `tests/test_phase7a_processing_status_golden.py`
- Add or modify: `tests/test_no_legacy_sse_core.py`

- [ ] **Step 1: Add AST/grep regression test**

Create `tests/test_no_legacy_sse_core.py`:

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOTS = [
    ROOT / "api_gateway",
    ROOT / "app_shell",
    ROOT / "delivery",
    ROOT / "execution",
    ROOT / "jobs",
]
PRODUCTION_FILES = [ROOT / "main.py"]


def test_no_production_legacy_sse_frame_emitters():
    offenders = []
    for root in PRODUCTION_ROOTS:
        for path in root.rglob("*.py"):
            text = path.read_text()
            if (
                "emit_legacy_frame" in text
                or "_emit_legacy_frame" in text
                or "_should_deliver_legacy" in text
            ):
                offenders.append(str(path.relative_to(ROOT)))
    for path in PRODUCTION_FILES:
        text = path.read_text()
        if (
            "emit_legacy_frame" in text
            or "_emit_legacy_frame" in text
            or "_should_deliver_legacy" in text
        ):
            offenders.append(str(path.relative_to(ROOT)))

    assert offenders == []


def test_no_production_room_raw_broadcasts():
    offenders = []
    for root in PRODUCTION_ROOTS:
        for path in root.rglob("*.py"):
            text = path.read_text()
            if "broadcast_to_room(" in text:
                offenders.append(str(path.relative_to(ROOT)))
    for path in PRODUCTION_FILES:
        text = path.read_text()
        if "broadcast_to_room(" in text:
            offenders.append(str(path.relative_to(ROOT)))

    assert offenders == []
```

- [ ] **Step 2: Run regression test and confirm it fails**

Run:

```bash
pytest tests/test_no_legacy_sse_core.py -q
```

Expected: FAIL with current legacy files listed.

- [ ] **Step 3: Delete legacy emit methods**

Remove after Task 5 has migrated every direct raw caller:

- `EventPublisherImpl._emit_legacy_frame()` from `delivery/event_publisher.py`
- `EventPublisherImpl._should_deliver_legacy()` from `delivery/event_publisher.py`
- `DeliveryFacade.emit_legacy_frame()` from `delivery/facade.py`
- `DeliveryCompatibility.emit_legacy_frame()` from `delivery/facade.py`
- `AppShellSSEManager.broadcast_to_room()` and `_emit_frame()` from `app_shell/delivery_runtime.py`

If `DeliveryCompatibility` has no remaining purpose after removal, delete the class and expose only explicit facade methods used by `AppShellSSEManager`.

- [ ] **Step 4: Update tests that asserted legacy behavior**

Delete or rewrite tests named like:

- `test_legacy_frame_path_preserves_frame_and_dedups_terminal_status`
- `test_broadcast_to_room_builds_legacy_frame`
- `test_send_processing_status_preserves_legacy_payload_and_skips_recording`
- `test_legacy_send_methods_have_golden_frame_shapes`

Replace with tests asserting typed DTO emission and final translated frame shape.

- [ ] **Step 5: Update legacy protocol/service tests**

Update or delete legacy raw-frame tests in:

- `tests/test_delivery_protocols.py`: remove assertions around `DeliveryCompatibility.emit_legacy_frame()`.
- `tests/test_service_sse.py`: replace `broadcast_to_room()` coverage with typed `send_*` helper coverage or connection/cancellation-only coverage.
- `tests/test_phase7a_processing_status_golden.py`: remove helper usage of `broadcast_to_room()` and assert the current typed processing-status/run-event ordering through `EventPublisher.emit()`.

These test files must not contain `broadcast_to_room(`, `emit_legacy_frame`, or `_emit_legacy_frame` after this step unless the text appears in a deleted-route deprecation assertion that is not imported from production code.

- [ ] **Step 6: Run delivery tests**

Run:

```bash
pytest \
  tests/test_delivery_event_publisher.py \
  tests/test_sse_adapter_delivery.py \
  tests/test_delivery_protocols.py \
  tests/test_service_sse.py \
  tests/test_phase7a_processing_status_golden.py \
  tests/test_no_legacy_sse_core.py \
  -q
```

Expected: PASS.

---

### Task 8: Update Architecture Documentation

**Files:**
- Modify: `docs/MODULAR_DECOUPLING_DESIGN.md`
- Modify: `System-Architecture.md`

- [ ] **Step 1: Update Delivery/SSE architecture docs**

Update `docs/MODULAR_DECOUPLING_DESIGN.md` so it no longer describes `DeliveryFacade.compat.emit_legacy_frame()`, legacy raw SSE frames, or legacy processing-status compatibility frames as active production architecture. Replace those sections with the final design:

- Frontend wire format is always `{type, timestamp, room_id, data}`.
- Backend modules emit typed `DeliveryEvent` DTOs.
- Delivery translator owns DTO-to-SSE frame translation.
- `ProcessingStatusEvent` supports all final statuses.
- `details` is `dict | None`.
- `RunEventNotification` and `ProcessingStatusEvent` ordering remains lifecycle record -> optional run event -> processing status.

Update `System-Architecture.md` where it references SSE delivery, Delivery, or run lifecycle SSE so it matches the final no-legacy design.

- [ ] **Step 2: Run documentation legacy scan**

Run:

```bash
rg -n "emit_legacy_frame|legacy raw SSE|compatibility frame|legacy processing-status|DeliveryFacade\\.compat" docs/MODULAR_DECOUPLING_DESIGN.md System-Architecture.md -S
```

Expected: no stale description of those paths as active production architecture. Historical changelog entries are acceptable only if clearly marked as past/removed.

---

### Task 9: Final Integration Verification

**Files:**
- No production code changes in this task.

- [ ] **Step 1: Run focused backend suite**

Run:

```bash
pytest \
  tests/test_delivery_translator.py \
  tests/test_common_a2a_constants.py \
  tests/test_common_foundation.py \
  tests/test_delivery_event_publisher.py \
  tests/test_delivery_sse_connection.py \
  tests/test_sse_adapter_delivery.py \
  tests/test_phase7_execution_event_gate.py \
  tests/test_phase7a_processing_status_gate.py \
  tests/test_agent_response_handler.py \
  tests/test_supervisor_improvements.py \
  tests/test_run_lifecycle_service.py \
  tests/test_service_hitl.py \
  tests/test_api_sse.py \
  tests/test_api_room_center.py \
  tests/test_scope_validation.py \
  tests/test_delivery_protocols.py \
  tests/test_service_sse.py \
  tests/test_phase7a_processing_status_golden.py \
  tests/test_no_legacy_sse_core.py \
  -q
```

Expected: PASS.

- [ ] **Step 2: Run import/type smoke check**

Run:

```bash
python -m compileall common delivery app_shell execution api_gateway jobs
```

Expected: all files compile without syntax errors.

- [ ] **Step 3: Run legacy text scan**

Run:

```bash
rg -n "legacy_details|LegacyProcessingStatus|emit_legacy_frame|_emit_legacy_frame|_should_deliver_legacy|broadcast_to_room\\(|target_group is fallback|createAndParseUserMessage" common delivery app_shell execution api_gateway jobs tests main.py -S \
  -g '!tests/test_no_legacy_sse_core.py'
```

Expected: no production hits. Test hits are acceptable only for regression tests that intentionally assert the banned strings are absent, and `tests/test_no_legacy_sse_core.py` is excluded from this scan. There should be no `createAndParseUserMessage` hits anywhere.

- [ ] **Step 4: Run architecture docs scan**

Run:

```bash
rg -n "emit_legacy_frame|legacy raw SSE|compatibility frame|legacy processing-status|DeliveryFacade\\.compat" docs/MODULAR_DECOUPLING_DESIGN.md System-Architecture.md -S
```

Expected: no stale description of removed legacy delivery paths as active architecture.

- [ ] **Step 5: Run full test suite if time permits**

Run:

```bash
pytest -q
```

Expected: PASS.

---

## Execution Notes

- Do not keep dual support for old frontend SSE frames. The frontend is assumed to be deployed with the final parser.
- Do not introduce a new frontend outer protocol using `event_type` / `payload`. That remains backend-internal DTO structure.
- Do not hand-roll raw frame dicts in Execution, Room, HITL, or AppShell. If a new frontend SSE type is needed, add a DTO in `common/dto/delivery.py` and a translator branch in `delivery/translator.py`.
- Keep business side effects before frontend emit: run lifecycle persistence must happen before `ProcessingStatusEvent` emission.
- Keep terminal dedup in Delivery for terminal `processing_status` frames.
