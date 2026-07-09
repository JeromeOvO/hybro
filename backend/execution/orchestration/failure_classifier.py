from __future__ import annotations

from uuid import uuid4

from common.utils.time import utcnow
from models.orchestration import OpenFailureRecord


def classify_agent_failure(
    *,
    agent_id: str,
    agent_message_id: str,
    error: str | None,
    status_message: str | None,
    dispatch_intent_id: str | None = None,
) -> OpenFailureRecord | None:
    code = _error_code(error=error, status_message=status_message)
    if code is None:
        return None
    message = error or status_message or code
    return OpenFailureRecord(
        failure_id=uuid4().hex,
        fingerprint=f"{agent_id}:{code}:{_fingerprint_message(message)}",
        source="a2a_adapter",
        agent_id=agent_id,
        agent_message_id=agent_message_id,
        dispatch_intent_id=dispatch_intent_id,
        error_code=code,
        error_message=message,
        recoverable=_is_recoverable(code),
        retry_count=0,
        max_retries=2,
        status="open",
        recovery_hints=_recovery_hints(code),
        updated_at=utcnow(),
    )


def _error_code(*, error: str | None, status_message: str | None) -> str | None:
    combined = f"{status_message or ''}\n{error or ''}".lower()
    if (
        "agent_does_not_accept_file_type" in combined
        or "does not accept the uploaded file type" in combined
    ):
        return "agent_does_not_accept_file_type"
    if "rate limit" in combined or "rate_limited" in combined:
        return "rate_limited"
    if "agent not found" in combined or "agent_unavailable" in combined:
        return "agent_unavailable"
    if "timeout" in combined or "timed out" in combined:
        return "timeout"
    if error or status_message:
        return "agent_execution_failed"
    return None


def _is_recoverable(code: str) -> bool:
    return code in {
        "agent_does_not_accept_file_type",
        "rate_limited",
        "agent_unavailable",
        "timeout",
        "agent_execution_failed",
    }


def _recovery_hints(code: str) -> list[str]:
    if code == "agent_does_not_accept_file_type":
        return ["retry_without_unsupported_attachments"]
    if code == "rate_limited":
        return ["retry_different_agent", "ask_user"]
    if code == "agent_unavailable":
        return ["retry_different_agent", "ask_user"]
    if code == "timeout":
        return ["retry_same_agent_with_smaller_context", "retry_different_agent"]
    return ["retry_with_refined_task", "ask_user"]


def _fingerprint_message(message: str) -> str:
    return " ".join(message.lower().split())[:160]
