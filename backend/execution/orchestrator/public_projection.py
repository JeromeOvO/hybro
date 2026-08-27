"""Public decision-visibility projection for the room SSE stream.

Legacy decision-visibility projection retained during the proposed
``backend/docs/Pi-Aligned-Turn-Lifecycle-Plan.md`` cutover: translate private
kernel lifecycle ``SessionEvent`` values into public ``run_event`` payload types
carried over the existing SSE ``run_event`` frame.

The payloads produced here are the only public surface for decisions, LLM
calls, retries, and tool calls. Raw system/user prompts, full tool arguments,
and reasoning text never leave this module: public payloads carry backend-
generated short summaries and metadata only.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from common.dto.pi_lifecycle import CANONICAL_RUN_EVENT_KINDS

from .lifecycle import SessionEvent
from .models import FrozenToolCatalogSnapshot, OrchestratorRunState
from .public_summaries import PublicSummaryRegistry
from .public_text import enforce_public_label_policy, sanitize_public_text

PUBLIC_RUN_EVENT_KINDS = CANONICAL_RUN_EVENT_KINDS
LEGACY_PUBLIC_RUN_EVENT_KINDS = frozenset(
    {
        "llm_call_completed",
        "llm_retry_scheduled",
        "orchestrator_decision",
        "tool_call_accepted",
        "tool_call_completed",
    }
)

_DEFAULT_SUMMARY_LIMIT = 240
_DEFAULT_ARG_KEYS = 8
_DEFAULT_ARG_VALUE_LIMIT = 120


@dataclass(frozen=True, slots=True)
class PublicRunEvent:
    """A ready-to-emit public decision-visibility event.

    ``kind`` is one of :data:`PUBLIC_RUN_EVENT_KINDS` and becomes the
    ``RunEventNotification.run_event_type`` over the wire.
    """

    kind: str
    event_id: str
    run_id: str
    seq: int
    room_id: str
    user_message_id: str | None
    client_request_id: str | None
    payload: dict[str, Any]


def _summary_text(value: Any, *, limit: int) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(value)
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…"


def _argument_summary(arguments: Any, *, limit: int = _DEFAULT_ARG_VALUE_LIMIT) -> Any:
    """Legacy compatibility fallback; arbitrary arguments are never public."""

    del arguments, limit
    return {}


def _content_text(parts: Any) -> str:
    text_parts: list[str] = []
    for part in parts or []:
        if isinstance(part, dict):
            if part.get("kind") == "text":
                text_parts.append(str(part.get("text") or ""))
            elif part.get("kind") == "data":
                data = part.get("data")
                if data is not None:
                    text_parts.append(_summary_text(data, limit=80))
            continue
        kind = getattr(part, "kind", None)
        if kind == "text":
            text_parts.append(str(getattr(part, "text", "") or ""))
        elif kind == "data":
            data = getattr(part, "data", None)
            if data is not None:
                text_parts.append(_summary_text(data, limit=80))
    return "\n".join(part for part in text_parts if part)


def canonical_settlement_payload(run: OrchestratorRunState) -> dict[str, Any]:
    """Map a settled aggregate to the closed public settlement payload."""
    settled_at = run.updated_at
    payload: dict[str, Any] = {
        "status": "failed" if run.status == "budget_exhausted" else run.status,
        "started_at": run.created_at,
        "settled_at": settled_at,
        "duration_ms": max(
            0, int((settled_at - run.created_at).total_seconds() * 1000)
        ),
    }
    if run.status == "completed":
        payload["final_message_id"] = run.proposed_final_message_id
    elif run.status == "canceled":
        payload["cancellation_code"] = run.cancellation_cause
    else:
        payload["failure_code"] = (
            "budget_exhausted" if run.status == "budget_exhausted" else "internal_error"
        )
        payload["error_summary"] = (
            "The run exhausted its execution budget."
            if run.status == "budget_exhausted"
            else "The run could not be completed."
        )
    return payload


class PublicProjectionTranslator:
    """SessionEvent → public decision-visibility payloads (sole public writer)."""

    def __init__(
        self,
        *,
        summary_limit: int = _DEFAULT_SUMMARY_LIMIT,
        lifecycle_family: str = "legacy",
        summary_registry: PublicSummaryRegistry | None = None,
        secret_values: tuple[str, ...] = (),
    ) -> None:
        if lifecycle_family not in {"legacy", "canonical"}:
            raise ValueError("unknown lifecycle family")
        self._summary_limit = summary_limit
        self._lifecycle_family = lifecycle_family
        self._summary_registry = summary_registry or PublicSummaryRegistry()
        self._secret_values = secret_values

    def _public_label(self, value: Any) -> str:
        return enforce_public_label_policy(value, secret_values=self._secret_values)

    def _execution_identity(
        self,
        raw: dict[str, Any],
        *,
        catalog: FrozenToolCatalogSnapshot | None,
    ) -> tuple[str, dict[str, str | None] | None, str]:
        """Resolve execution kind, safe target, and public trace label.

        A frozen catalog entry identifies an A2A Agent Execution; every other
        invocation (e.g. structured system actions) is a plain Tool Execution.
        Registry ids never enter public payloads; only the base Agent name and
        the opaque public call id are exposed.
        """
        raw_tool_name = str(raw.get("tool_name") or "")
        entry = next(
            (
                item
                for item in (catalog.entries if catalog is not None else [])
                if item.definition.name == raw_tool_name
            ),
            None,
        )
        trace_label = self._public_label(
            raw.get("agent_label") or raw_tool_name or "tool"
        )
        if entry is None:
            return "tool", None, trace_label
        base_name = self._public_label(
            entry.agent_display_name or entry.definition.label.strip() or trace_label
        )
        return "agent", {"name": base_name, "source": None}, trace_label

    def translate(
        self,
        event: SessionEvent,
        *,
        catalog: FrozenToolCatalogSnapshot | None = None,
    ) -> PublicRunEvent | None:
        if self._lifecycle_family == "canonical":
            return self._translate_canonical(event, catalog=catalog)
        kind = self._public_kind(event)
        if kind is None:
            return None
        payload = self._payload(kind, event)
        if payload is None:
            return None
        return PublicRunEvent(
            kind=kind,
            event_id=f"public:{event.run_id}:{kind}:{event.sequence}",
            run_id=event.run_id,
            seq=event.sequence,
            room_id=event.room_id or "",
            user_message_id=event.user_message_id or event.causation_id,
            client_request_id=event.client_request_id,
            payload=payload,
        )

    def _translate_canonical(
        self,
        event: SessionEvent,
        *,
        catalog: FrozenToolCatalogSnapshot | None,
    ) -> PublicRunEvent | None:
        raw = event.payload or {}
        kind = {
            "run_started": "run_started",
            "turn_started": "turn_start",
            "message_started": "message_start",
            "message_updated": "message_update",
            "message_completed": "message_end",
            "tool_execution_started": "tool_execution_start",
            "tool_execution_updated": "tool_execution_update",
            "tool_execution_completed": "tool_execution_end",
            "turn_completed": "turn_end",
            "model_retry_scheduled": "retry_scheduled",
        }.get(event.event_type)
        if kind is None:
            return None
        payload = self._canonical_payload(kind, event, raw, catalog=catalog)
        if payload is None:
            return None
        semantic = str(
            _canonical_semantic_id(kind, event, raw)
            if kind == "run_started"
            else raw.get("public_event_id") or _canonical_semantic_id(kind, event, raw)
        )
        return PublicRunEvent(
            kind=kind,
            event_id=f"public:{event.run_id}:{kind}:{semantic}",
            run_id=event.run_id,
            seq=event.sequence,
            room_id=event.room_id or "",
            user_message_id=event.user_message_id or event.causation_id,
            client_request_id=event.client_request_id,
            payload=payload,
        )

    def _canonical_payload(  # noqa: C901
        self,
        kind: str,
        event: SessionEvent,
        raw: dict[str, Any],
        *,
        catalog: FrozenToolCatalogSnapshot | None,
    ) -> dict[str, Any] | None:
        internal_turn_id = str(raw.get("internal_turn_id") or "")
        message_id = str(raw.get("message_id") or "")
        if kind == "run_started":
            return {
                "hybro_turn_id": event.run_id,
                "user_message_id": event.user_message_id or event.causation_id,
                "started_at": raw.get("started_at") or event.timestamp,
                "mode": str(raw.get("mode") or "supervisor"),
            }
        if kind == "turn_start":
            return {
                "internal_turn_id": internal_turn_id,
                "attempt": _as_int(raw.get("attempt")) or 1,
            }
        if kind == "message_start":
            return {
                "internal_turn_id": internal_turn_id,
                "message_id": message_id,
                "role": "assistant",
            }
        if kind == "message_update":
            return {
                "internal_turn_id": internal_turn_id,
                "message_id": message_id,
                "assistant_message_event": {
                    "type": "text_delta",
                    "content_index": _as_int(raw.get("content_index")) or 0,
                    "delta_index": _as_int(raw.get("delta_index")) or 0,
                    "start_offset": _as_int(raw.get("start_offset")) or 0,
                    "end_offset": _as_int(raw.get("end_offset")) or 0,
                    "delta": str(raw.get("delta") or ""),
                },
            }
        if kind == "message_end":
            if raw.get("message_kind") == "tool_result":
                return None
            payload = {
                "internal_turn_id": internal_turn_id,
                "message_id": message_id,
                "stop_reason": raw.get("stop_reason"),
                "disposition": raw.get("disposition"),
                "text": str(raw.get("text") or ""),
            }
            if raw.get("error_summary") is not None:
                payload["error_summary"] = str(raw["error_summary"])
            return payload
        if kind == "tool_execution_start":
            execution_kind, target, tool_name = self._execution_identity(
                raw, catalog=catalog
            )
            return {
                "internal_turn_id": internal_turn_id,
                "tool_call_id": str(raw.get("public_call_id") or ""),
                "tool_name": tool_name,
                "input": self._summary_registry.input_summary(
                    str(raw.get("tool_name") or ""),
                    raw.get("arguments"),
                    catalog=catalog,
                ),
                "execution_kind": execution_kind,
                **({} if target is None else {"target": target}),
                "request_summary": _public_request_summary(
                    raw.get("arguments"), self._summary_limit
                ),
            }
        if kind == "tool_execution_update":
            execution_kind, target, tool_name = self._execution_identity(
                raw, catalog=catalog
            )
            return {
                "internal_turn_id": internal_turn_id,
                "tool_call_id": str(raw.get("public_call_id") or ""),
                "tool_name": tool_name,
                "update_index": _as_int(raw.get("update_index")) or 1,
                "status": raw.get("status"),
                "partial_result": self._summary_registry.result_summary(
                    str(raw.get("tool_name") or ""),
                    raw.get("partial_result"),
                    catalog=catalog,
                ),
                "execution_kind": execution_kind,
                **({} if target is None else {"target": target}),
            }
        if kind == "tool_execution_end":
            private_status = str(raw.get("result_status") or raw.get("status") or "")
            outcome = {
                "completed": "completed",
                "canceled": "canceled",
                "rejected": "failed",
                "expired": "failed",
            }.get(private_status, "failed")
            failure_reason = (
                private_status
                if private_status in {"rejected", "expired"}
                else ("execution" if outcome == "failed" else None)
            )
            execution_kind, target, tool_name = self._execution_identity(
                raw, catalog=catalog
            )
            payload = {
                "internal_turn_id": internal_turn_id,
                "tool_call_id": str(raw.get("public_call_id") or ""),
                "tool_name": tool_name,
                "outcome": outcome,
                "result": (
                    ""
                    if outcome == "canceled" or execution_kind == "agent"
                    else self._summary_registry.result_summary(
                        str(raw.get("tool_name") or ""),
                        raw.get("result_text"),
                        catalog=catalog,
                    )
                ),
                "is_error": outcome == "failed",
                "duration_ms": _as_int(raw.get("duration_ms")) or 0,
                "execution_kind": execution_kind,
                **({} if target is None else {"target": target}),
                "detail_available": (
                    execution_kind == "agent" and outcome == "completed"
                ),
            }
            if failure_reason is not None:
                payload["failure_reason"] = failure_reason
            return payload
        if kind == "turn_end":
            return {
                "internal_turn_id": internal_turn_id,
                "message_id": message_id or None,
                "tool_call_ids": list(raw.get("tool_call_ids") or []),
                "status": str(raw.get("status") or "completed"),
            }
        if kind == "retry_scheduled":
            private_error = str(raw.get("error_class") or "provider_error")
            error_class = {
                "timeout": "provider_timeout",
                "content_filter": "content_filter",
                "assembly_error": "assembly_error",
                "tool_failure": "tool_failure",
                "process_restart": "process_restart",
            }.get(private_error, "provider_error")
            return {
                "internal_turn_id": internal_turn_id,
                "attempt": max(2, _as_int(raw.get("attempt")) or 2),
                "delay_ms": _as_int(raw.get("retry_delay_ms")) or 0,
                "error_class": error_class,
            }
        return None

    @staticmethod
    def _public_kind(event: SessionEvent) -> str | None:
        return {
            "model_retry_scheduled": "llm_retry_scheduled",
            "model_turn_completed": "llm_call_completed",
            "orchestrator_decision": "orchestrator_decision",
            "tool_execution_started": "tool_call_accepted",
            "tool_execution_completed": "tool_call_completed",
        }.get(event.event_type)

    def _payload(self, kind: str, event: SessionEvent) -> dict[str, Any] | None:
        raw = event.payload or {}
        return {
            "llm_retry_scheduled": self._retry_payload,
            "llm_call_completed": self._llm_call_payload,
            "orchestrator_decision": self._decision_payload,
            "tool_call_accepted": self._tool_accepted_payload,
            "tool_call_completed": self._tool_completed_payload,
        }.get(kind, lambda _event, _raw: None)(event, raw)

    @staticmethod
    def _retry_payload(_event: SessionEvent, raw: dict[str, Any]) -> dict[str, Any]:
        # ``retryable`` is intentionally redacted from the public payload.
        return {
            "attempt": _as_int(raw.get("attempt")),
            "error_class": str(raw.get("error_class") or ""),
            "retry_delay_ms": _as_int(raw.get("retry_delay_ms")),
        }

    @staticmethod
    def _llm_call_payload(_event: SessionEvent, raw: dict[str, Any]) -> dict[str, Any]:
        usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
        payload: dict[str, Any] = {
            "model": str(raw.get("model") or ""),
            "provider": str(raw.get("provider") or ""),
            "attempt": _as_int(raw.get("attempt")),
            "outcome": str(raw.get("outcome") or "unknown"),
            "duration_ms": _as_int(raw.get("duration_ms")),
            "usage": {
                "input": _as_int(usage.get("input")),
                "output": _as_int(usage.get("output")),
            },
        }
        finish_reason = raw.get("finish_reason")
        if finish_reason is not None:
            payload["finish_reason"] = str(finish_reason)
        return payload

    def _decision_payload(
        self, _event: SessionEvent, raw: dict[str, Any]
    ) -> dict[str, Any] | None:
        plan_steps = []
        chosen_agents: list[str] = []
        for step in raw.get("plan_steps") or []:
            if not isinstance(step, dict):
                continue
            agent = str(step.get("agent") or "").strip()
            if not agent:
                continue
            chosen_agents.append(agent)
            plan_steps.append(
                {
                    "agent": agent,
                    "summary": self._summarize(step.get("summary")),
                }
            )
        if not plan_steps:
            return None
        return {
            "chosen_agents": chosen_agents,
            "plan_steps": plan_steps,
            "reason": self._summarize(raw.get("reason")),
        }

    @staticmethod
    def _tool_accepted_payload(
        event: SessionEvent, raw: dict[str, Any]
    ) -> dict[str, Any] | None:
        tool_name = str(raw.get("agent_label") or raw.get("tool_name") or "").strip()
        if not tool_name:
            return None
        return {
            "call_id": _public_call_id(event, raw),
            "tool_name": tool_name,
            "arg_summary": _argument_summary(raw.get("arguments")),
        }

    def _tool_completed_payload(
        self, _event: SessionEvent, raw: dict[str, Any]
    ) -> dict[str, Any] | None:
        tool_name = str(raw.get("agent_label") or raw.get("tool_name") or "").strip()
        if not tool_name:
            return None
        return {
            "call_id": _public_call_id(_event, raw),
            "tool_name": tool_name,
            "result_summary": "",
            "exit_code": _exit_code(raw),
            "duration_ms": _as_int(raw.get("duration_ms")),
        }

    def _summarize(self, value: Any) -> str:
        return _summary_text(value, limit=self._summary_limit)


def _public_call_id(event: SessionEvent, raw: dict[str, Any]) -> str:
    private_call_id = str(raw.get("call_id") or "")
    if not private_call_id:
        return ""
    digest = hashlib.sha256(f"{event.run_id}:{private_call_id}".encode()).hexdigest()[
        :16
    ]
    return f"inv_{digest}"


def _canonical_semantic_id(  # noqa: C901
    kind: str, event: SessionEvent, raw: dict[str, Any]
) -> str:
    """Restart-stable identity for every canonical lifecycle boundary."""

    turn_id = str(raw.get("internal_turn_id") or "root")
    message_id = str(raw.get("message_id") or "none")
    call_id = str(raw.get("public_call_id") or raw.get("call_id") or "none")
    if kind == "run_started":
        return f"{event.run_id}:run_started"
    if kind == "turn_start":
        return f"{turn_id}:turn_start:{_as_int(raw.get('attempt')) or 1}"
    if kind == "message_start":
        return f"{turn_id}:{message_id}:message_start"
    if kind == "message_update":
        return str(
            raw.get("public_event_id")
            or f"{turn_id}:{message_id}:update:{raw.get('start_offset')}:{raw.get('end_offset')}"
        )
    if kind == "message_end":
        return f"{turn_id}:{message_id}:message_end"
    if kind == "tool_execution_start":
        return f"{turn_id}:{call_id}:tool_start"
    if kind == "tool_execution_update":
        return (
            f"{turn_id}:{call_id}:tool_update:{_as_int(raw.get('update_index')) or 0}"
        )
    if kind == "tool_execution_end":
        return f"{turn_id}:{call_id}:tool_end"
    if kind == "turn_end":
        return f"{turn_id}:turn_end:{raw.get('status') or 'completed'}"
    if kind == "retry_scheduled":
        return f"{turn_id}:retry:{_as_int(raw.get('attempt')) or 2}"
    raise ValueError(f"unsupported canonical kind {kind}")


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _public_request_summary(arguments: Any, limit: int) -> str:
    """Extract the safe Agent Card request description from tool arguments.

    Agent calls carry the model-authored ``task`` question. Anything that is
    not a short, plain string closes to an empty summary rather than exposing
    raw argument material.
    """
    if not isinstance(arguments, dict):
        return ""
    task = arguments.get("task")
    if not isinstance(task, str):
        return ""
    text = task.strip()
    if not text:
        return ""
    return sanitize_public_text(text)[:limit]


def _exit_code(raw: dict[str, Any]) -> int | None:
    exit_code = raw.get("exit_code")
    if exit_code is not None:
        return _as_int(exit_code)
    status = str(raw.get("result_status") or "")
    error_code = raw.get("result_error_code")
    if status == "completed":
        return 0
    if error_code is not None and (numeric := _as_int(error_code)) is not None:
        return numeric
    if status in {"failed", "canceled", "rejected", "expired"}:
        return 1
    return None


__all__ = [
    "LEGACY_PUBLIC_RUN_EVENT_KINDS",
    "PUBLIC_RUN_EVENT_KINDS",
    "PublicProjectionTranslator",
    "PublicRunEvent",
]
