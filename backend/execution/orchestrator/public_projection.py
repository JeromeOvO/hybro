"""Public decision-visibility projection for the room SSE stream.

Phase 1 of the Room Stream Snapshot plan (``backend/docs/Room-Stream-Snapshot-Plan.md``
§6): translate private kernel lifecycle ``SessionEvent`` values into public
``run_event`` payload types carried over the existing SSE ``run_event`` frame.

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

from .lifecycle import SessionEvent

# Public run_event payload types delivered by Phase 1.
PUBLIC_RUN_EVENT_KINDS = frozenset(
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
    """Redacted tool-argument summary: keys plus short scalar values only.

    Nested structures and long values are never copied into the public
    payload; they are collapsed to short type summaries.
    """

    if not isinstance(arguments, dict):
        return {"count": len(arguments)} if isinstance(arguments, list) else None
    summary: dict[str, Any] = {}
    for key, value in list(arguments.items())[:_DEFAULT_ARG_KEYS]:
        if value is None or isinstance(value, (int, float, bool)):
            summary[str(key)] = value
        elif isinstance(value, str):
            summary[str(key)] = _summary_text(value, limit=limit)
        elif isinstance(value, dict):
            summary[str(key)] = {"object_keys": list(value)[:8]}
        elif isinstance(value, list):
            summary[str(key)] = {"list_len": len(value)}
        else:
            summary[str(key)] = str(type(value).__name__)
    if len(arguments) > _DEFAULT_ARG_KEYS:
        summary["…"] = f"{len(arguments) - _DEFAULT_ARG_KEYS} more keys"
    return summary


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


class PublicProjectionTranslator:
    """SessionEvent → public decision-visibility payloads (sole public writer)."""

    def __init__(self, *, summary_limit: int = _DEFAULT_SUMMARY_LIMIT) -> None:
        self._summary_limit = summary_limit

    def translate(self, event: SessionEvent) -> PublicRunEvent | None:
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
            "result_summary": self._summarize(raw.get("result_text")),
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


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _exit_code(raw: dict[str, Any]) -> int | None:
    exit_code = raw.get("exit_code")
    if exit_code is not None:
        return _as_int(exit_code)
    status = str(raw.get("result_status") or "")
    error_code = raw.get("result_error_code")
    if status == "completed":
        return 0
    if error_code is not None:
        return _as_int(error_code)
    return None


__all__ = [
    "PUBLIC_RUN_EVENT_KINDS",
    "PublicProjectionTranslator",
    "PublicRunEvent",
]
