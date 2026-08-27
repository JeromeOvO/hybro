"""Snapshot materialization for the room stream (Room Stream Snapshot plan §5).

Snapshots are produced by folding ``room_events[0..N]`` through the same fold
logic the client uses (P1/P4). The fold is incrementally materialized: an
in-memory checkpoint at ``room_seq`` M plus a fold of M+1..N on demand keeps
long rooms cheap. ``force=True`` refolds from the authoritative log (used by
the ``?snapshot=1`` recovery path to rule out checkpoint staleness).
"""

from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Callable
from datetime import datetime
from typing import Any

from common.utils.logger import get_logger
from delivery.room_events import RoomEventStore

logger = get_logger(__name__)

_CHECKPOINT_CAPACITY = 512
_SNAPSHOT_READ_LIMIT = 2000
_TERMINAL_TASK_STATUSES = frozenset(
    {"completed", "failed", "canceled", "rejected", "expired"}
)
_TERMINAL_PROCESSING_STATUSES = frozenset(
    {"completed", "failed", "canceled", "rejected", "rate_limited", "error"}
)
_TRACE_KINDS = frozenset(
    {
        "llm_call_completed",
        "llm_retry_scheduled",
        "orchestrator_decision",
        "tool_call_accepted",
        "tool_call_completed",
    }
)


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _first_present(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if data.get(key) is not None:
            return data[key]
    return None


class RoomEventFold:
    """Pure fold: room event records → snapshot state (mirrors client folds)."""

    def __init__(self) -> None:
        self.messages: dict[str, dict[str, Any]] = {}
        self.tasks: dict[str, dict[str, Any]] = {}
        self.runs: dict[str, dict[str, Any]] = {}
        self.hitl_requests: dict[str, dict[str, Any]] = {}
        self.hitl_resolved: list[dict[str, Any]] = []
        self.streaming: dict[str, dict[str, Any]] = {}
        self.trace: dict[str, dict[str, Any]] = {}

    def apply(self, record: dict[str, Any]) -> None:
        kind = record.get("kind")
        data = record.get("payload_public") or {}
        handler = {
            "processing_status": self._processing_status,
            "agent_response_partial": self._agent_response_partial,
            "agent_response": self._agent_response,
            "task_submitted": self._task_submitted,
            "task_update": self._task_update,
            "artifact_update": self._artifact_update,
            "error": self._error,
            "cancellation": self._cancellation,
            "run_event": self._run_event,
            "hitl_request": self._hitl_request,
            "hitl_response": self._hitl_response,
        }.get(kind)
        if handler is not None:
            handler(data, record)

    # ── message-level folds ─────────────────────────────────────────────

    def _message(self, message_id: Any) -> dict[str, Any]:
        if not message_id:
            return {}
        key = str(message_id)
        return self.messages.setdefault(
            key,
            {
                "message_id": key,
                "agent_id": None,
                "agent_name": None,
                "content": None,
                "parts": None,
                "related_message_id": None,
                "client_request_id": None,
                "status": None,
                "task_status": None,
                "task_content": None,
                "task_error": None,
                "requires_input": False,
                "requires_auth": False,
                "step_number": None,
                "total_steps": None,
                "created_at": None,
                "ts": None,
                "artifacts": None,
                "status_logs": [],
            },
        )

    def _processing_status(self, data: dict[str, Any], record: dict[str, Any]) -> None:
        message = self._message(data.get("message_id"))
        status = data.get("status")
        if status in _TERMINAL_PROCESSING_STATUSES:
            message["status"] = status
        details = data.get("details")
        if isinstance(details, dict):
            log_message = _first_present(
                details, "message", "status_message", "stage", "description"
            )
            if not isinstance(log_message, str) or not log_message.strip():
                log_message = json.dumps(
                    details,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                )
                if log_message == "{}":
                    log_message = None
            if log_message:
                entry = {
                    "message": str(log_message).strip(),
                    "timestamp": str(record.get("ts") or ""),
                }
                turn_phase = details.get("turn_phase")
                if turn_phase in {"collecting", "synthesizing", "terminal"}:
                    entry["turn_phase"] = turn_phase
                message["status_logs"].append(entry)
        self._carry_correlation(message, data)

    def _agent_response_partial(
        self, data: dict[str, Any], record: dict[str, Any]
    ) -> None:
        message_id = str(data.get("message_id") or "")
        if not message_id:
            return
        buffer = self.streaming.setdefault(
            message_id,
            {
                "message_id": message_id,
                "agent_id": None,
                "text": "",
                "artifacts": [],
                "is_complete": False,
                "client_request_id": None,
                "last_chunk": False,
            },
        )
        buffer["agent_id"] = data.get("agent_id") or buffer["agent_id"]
        delta = data.get("content_delta")
        if isinstance(delta, str):
            buffer["text"] = f"{buffer['text']}{delta}"
        buffer["client_request_id"] = (
            _first_present(data, "client_request_id", "correlation_id")
            or buffer["client_request_id"]
        )

    def _agent_response(self, data: dict[str, Any], record: dict[str, Any]) -> None:
        message_id = str(data.get("message_id") or "")
        if not message_id:
            return
        message = self._message(message_id)
        message["agent_id"] = data.get("agent_id")
        message["content"] = data.get("content")
        if data.get("parts") is not None:
            message["parts"] = data["parts"]
        message["ts"] = str(record.get("ts") or "") or message["ts"]
        self._carry_correlation(message, data)
        message["related_message_id"] = (
            data.get("related_message_id") or message["related_message_id"]
        )
        # The terminal commit supersedes any partial buffer.
        self.streaming.pop(message_id, None)

    def _task_submitted(self, data: dict[str, Any], record: dict[str, Any]) -> None:
        task_id = str(data.get("task_id") or "")
        message_id = str(data.get("message_id") or "")
        task = self.tasks.setdefault(
            task_id,
            {
                "task_id": task_id,
                "message_id": message_id,
                "agent_name": None,
                "agent_id": None,
                "status": None,
                "requires_input": False,
                "requires_auth": False,
                "content": None,
                "status_message": None,
                "step_number": None,
                "total_steps": None,
                "task_content": None,
                "created_at": None,
                "error": None,
            },
        )
        task["message_id"] = message_id or task["message_id"]
        task["agent_name"] = data.get("agent_name")
        task["agent_id"] = data.get("agent_id")
        task["status"] = data.get("status")
        task["created_at"] = data.get("created_at") or task["created_at"]
        task["step_number"] = data.get("step_number")
        task["total_steps"] = data.get("total_steps")
        task["task_content"] = data.get("task_content") or task["task_content"]
        message = self._message(message_id)
        message["agent_id"] = data.get("agent_id") or message["agent_id"]
        message["agent_name"] = data.get("agent_name") or message["agent_name"]
        message["task_status"] = data.get("status")
        message["task_content"] = data.get("task_content")
        message["related_message_id"] = (
            data.get("related_message_id") or message["related_message_id"]
        )
        message["created_at"] = data.get("created_at") or message["created_at"]
        message["step_number"] = data.get("step_number")
        message["total_steps"] = data.get("total_steps")
        self._carry_correlation(message, data)

    def _task_update(self, data: dict[str, Any], record: dict[str, Any]) -> None:
        message_id = str(data.get("message_id") or "")
        status = data.get("status")
        for task in self.tasks.values():
            if message_id and task.get("message_id") == message_id:
                task["status"] = status
                task["content"] = data.get("content") or task["content"]
                task["error"] = data.get("error") or task["error"]
                task["status_message"] = (
                    data.get("status_message") or task["status_message"]
                )
                task["requires_input"] = bool(
                    data.get("requires_input", task.get("requires_input"))
                )
                task["requires_auth"] = bool(
                    data.get("requires_auth", task.get("requires_auth"))
                )
        message = self._message(message_id)
        message["task_status"] = status
        message["agent_name"] = data.get("agent_name") or message["agent_name"]
        message["task_error"] = data.get("error") or message["task_error"]
        message["requires_input"] = bool(data.get("requires_input", False))
        message["requires_auth"] = bool(data.get("requires_auth", False))
        if data.get("content") is not None:
            message["content"] = data["content"]
        if data.get("parts") is not None:
            message["parts"] = data["parts"]
        message["related_message_id"] = (
            data.get("related_message_id") or message["related_message_id"]
        )
        message["created_at"] = data.get("created_at") or message["created_at"]
        message["step_number"] = data.get("step_number")
        message["total_steps"] = data.get("total_steps")
        self._carry_correlation(message, data)
        if status in _TERMINAL_TASK_STATUSES:
            self.streaming.pop(message_id, None)

    def _artifact_update(self, data: dict[str, Any], record: dict[str, Any]) -> None:
        message_id = str(data.get("message_id") or "")
        if not message_id or data.get("last_chunk") is not True:
            return
        artifact = data.get("artifact")
        if not isinstance(artifact, dict):
            return
        buffer = self.streaming.setdefault(
            message_id,
            {
                "message_id": message_id,
                "agent_id": None,
                "text": "",
                "artifacts": [],
                "is_complete": False,
                "client_request_id": None,
                "last_chunk": False,
            },
        )
        buffer["agent_id"] = data.get("agent_id") or buffer["agent_id"]
        is_append = bool(data.get("append"))
        chunks = buffer["artifacts"]
        if not is_append or not chunks:
            chunks.append(artifact)
        else:
            existing = chunks[-1]
            if isinstance(existing, dict):
                merged = dict(existing)
                for part in artifact.get("parts") or []:
                    merged.setdefault("parts", []).append(part)
                chunks[-1] = merged
            else:
                chunks.append(artifact)
        buffer["last_chunk"] = True
        buffer["is_complete"] = True
        buffer["client_request_id"] = (
            _first_present(data, "client_request_id", "correlation_id")
            or buffer["client_request_id"]
        )

    def _error(self, data: dict[str, Any], record: dict[str, Any]) -> None:
        message_id = data.get("message_id")
        if not message_id:
            return
        message = self._message(message_id)
        message["task_error"] = str(data.get("error") or "")
        self._carry_correlation(message, data)

    def _cancellation(self, data: dict[str, Any], record: dict[str, Any]) -> None:
        message_id = str(data.get("message_id") or "")
        if not message_id:
            return
        message = self._message(message_id)
        message["status"] = "canceled"
        message["task_status"] = "canceled"

    def _run_event(self, data: dict[str, Any], record: dict[str, Any]) -> None:
        run_id = str(data.get("run_id") or "")
        sub_type = data.get("type")
        if sub_type in {"run_completed", "run_failed", "run_canceled"} and run_id:
            trace_run = self.trace.get(run_id) or {}
            self.runs[run_id] = {
                "run_id": run_id,
                "status": str(sub_type).removeprefix("run_"),
                "client_request_id": (
                    data.get("correlation_id") or trace_run.get("client_request_id")
                ),
                "ts": str(record.get("ts") or ""),
            }
        if sub_type in _TRACE_KINDS and run_id:
            self._fold_trace(run_id, sub_type, data, record)

    def _fold_trace(
        self,
        run_id: str,
        sub_type: str,
        data: dict[str, Any],
        record: dict[str, Any],
    ) -> None:
        trace_run = self.trace.setdefault(
            run_id,
            {
                "run_id": run_id,
                "client_request_id": None,
                "nodes": [],
                "usage": None,
                "duration_ms": 0,
            },
        )
        correlation_id = data.get("correlation_id")
        if correlation_id:
            trace_run["client_request_id"] = correlation_id
        payload = data.get("payload") or {}
        node_id = f"{run_id}:{sub_type}:{data.get('event_id') or ''}"
        node_base = {
            "id": node_id,
            "client_request_id": correlation_id or trace_run["client_request_id"],
            "ts": str(record.get("ts") or ""),
        }
        if sub_type == "llm_call_completed":
            node = {
                **node_base,
                "kind": "llm_call",
                "model": payload.get("model"),
                "provider": payload.get("provider"),
                "attempt": _as_int(payload.get("attempt")),
                "outcome": payload.get("outcome"),
                "duration_ms": _as_int(payload.get("duration_ms")),
                "usage": payload.get("usage"),
                "finish_reason": payload.get("finish_reason"),
            }
            trace_run["nodes"].append(node)
            duration = _as_int(payload.get("duration_ms")) or 0
            trace_run["duration_ms"] = int(trace_run["duration_ms"]) + duration
            if payload.get("usage"):
                trace_run["usage"] = payload["usage"]
        elif sub_type == "llm_retry_scheduled":
            trace_run["nodes"].append(
                {
                    **node_base,
                    "kind": "retry",
                    "attempt": _as_int(payload.get("attempt")),
                    "error_class": payload.get("error_class"),
                    "retry_delay_ms": _as_int(payload.get("retry_delay_ms")),
                }
            )
        elif sub_type == "orchestrator_decision":
            trace_run["nodes"].append(
                {
                    **node_base,
                    "kind": "decision",
                    "chosen_agents": payload.get("chosen_agents"),
                    "plan_steps": payload.get("plan_steps"),
                    "reason": payload.get("reason"),
                }
            )
        elif sub_type in {"tool_call_accepted", "tool_call_completed"}:
            tool_name = payload.get("tool_name") or "unknown"
            call_id = payload.get("call_id") or tool_name
            merged_id = f"{run_id}:tool_call:{call_id}"
            existing = next(
                (
                    node
                    for node in trace_run["nodes"]
                    if node.get("kind") == "tool_call" and node.get("id") == merged_id
                ),
                None,
            )
            if existing is None:
                existing = {
                    **node_base,
                    "id": merged_id,
                    "kind": "tool_call",
                    "call_id": payload.get("call_id"),
                    "tool_name": tool_name,
                    "status": None,
                    "arg_summary": None,
                    "result_summary": None,
                    "exit_code": None,
                    "duration_ms": None,
                }
                trace_run["nodes"].append(existing)
            if correlation_id:
                existing["client_request_id"] = correlation_id
            existing["ts"] = str(record.get("ts") or "")
            if sub_type == "tool_call_accepted":
                existing["status"] = "accepted"
                existing["arg_summary"] = payload.get("arg_summary")
            else:
                existing["status"] = "completed"
                existing["result_summary"] = payload.get("result_summary")
                existing["exit_code"] = _as_int(payload.get("exit_code"))
                existing["duration_ms"] = _as_int(payload.get("duration_ms"))

    def _hitl_request(self, data: dict[str, Any], record: dict[str, Any]) -> None:
        request_id = str(data.get("request_id") or "")
        if not request_id:
            return
        self.hitl_requests[request_id] = dict(data)
        self.hitl_requests[request_id]["ts"] = str(record.get("ts") or "")

    def _hitl_response(self, data: dict[str, Any], record: dict[str, Any]) -> None:
        request_id = str(data.get("request_id") or "")
        entry = dict(data)
        entry["ts"] = str(record.get("ts") or "")
        self.hitl_resolved.append(entry)
        if request_id and request_id in self.hitl_requests:
            existing = self.hitl_requests[request_id]
            existing["status"] = data.get("status")
            existing["interaction_id"] = data.get("interaction_id")
            existing["interaction_status"] = data.get("interaction_status")
            existing["interaction_version"] = data.get("interaction_version")
            existing["application_status"] = data.get("application_status")

    @staticmethod
    def _carry_correlation(message: dict[str, Any], data: dict[str, Any]) -> None:
        client_request_id = _first_present(data, "client_request_id", "correlation_id")
        if client_request_id:
            message["client_request_id"] = client_request_id

    def state(self, *, room_seq: int) -> dict[str, Any]:
        return {
            "room_seq": room_seq,
            "messages": list(self.messages.values()),
            "tasks": list(self.tasks.values()),
            "runs": list(self.runs.values()),
            "hitl": {
                "requests": list(self.hitl_requests.values()),
                "resolved": list(self.hitl_resolved),
            },
            "streaming": self.streaming,
            "trace": self.trace,
        }


class SnapshotService:
    """Incremental materialized snapshot producer over the room event log."""

    def __init__(
        self,
        *,
        store: RoomEventStore,
        capacity: int = _CHECKPOINT_CAPACITY,
        read_limit: int = _SNAPSHOT_READ_LIMIT,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._capacity = capacity
        self._read_limit = read_limit
        self._now = now or (lambda: datetime.now())
        # room_id → (watermark room_seq, folded state dict)
        self._checkpoints: OrderedDict[str, tuple[int, RoomEventFold]] = OrderedDict()

    async def snapshot(self, room_id: str, *, force: bool = False) -> dict[str, Any]:
        watermark = 0
        fold = RoomEventFold()
        if not force:
            cached = self._checkpoints.get(room_id)
            if cached is not None:
                watermark, fold = cached

        records = await self._store.read_range(
            room_id, after=watermark, limit=self._read_limit, include_skipped=True
        )
        expected = watermark + 1
        folded_any = False
        for record in records:
            room_seq = int(record.get("room_seq") or 0)
            if room_seq < expected:
                continue
            if room_seq > expected:
                # Contiguous-prefix rule (§11 risk 2): the fold never skips a
                # missing seq — it stops at the first gap. Permanent holes are
                # healed by ``skipped`` tombstones the store backfills, which
                # arrive in sequence order and advance the fold.
                break
            if record.get("kind") != "skipped":
                fold.apply(record)
                folded_any = True
            expected += 1
        watermark = expected - 1

        if force or folded_any or watermark > 0:
            self._remember(room_id, watermark, fold)
        return fold.state(room_seq=watermark)

    async def latest_seq(self, room_id: str) -> int:
        return await self._store.latest_seq(room_id)

    def _remember(self, room_id: str, watermark: int, fold: RoomEventFold) -> None:
        self._checkpoints[room_id] = (watermark, fold)
        self._checkpoints.move_to_end(room_id)
        while len(self._checkpoints) > self._capacity:
            self._checkpoints.popitem(last=False)


__all__ = ["RoomEventFold", "SnapshotService"]
