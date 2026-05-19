from __future__ import annotations

from common.a2a_constants import (
    CommonTaskState,
    INTERACTIVE_STATES,
    is_failure_state,
    is_terminal_state,
)
from common.dto import HubAgentResponseInternal
from common.utils.time import utcnow
from hub_runtime_bridge.idempotency import (
    ingest_idempotency_key,
    legacy_correlation_fingerprint,
    stable_response_key,
)

TERMINAL_AGENT_EVENT_KINDS = {"response", "error", "canceled"}
UNAUTHORIZED_EVENT = object()
LEGACY_RELAY_EVENT_KIND_MAP = {
    "agent_response": "response",
    "agent_error": "error",
    "task_status": "status_update",
    "task_interactive": "interactive",
}
SUPPORTED_HUB_PUBLISH_EVENT_TYPES = set(LEGACY_RELAY_EVENT_KIND_MAP) | {
    "artifact_update",
    "canceled",
    "error",
    "interactive",
    "processing_status",
    "response",
    "status_update",
    "task_submitted",
}


class HubPublishService:
    def __init__(
        self,
        *,
        journal=None,
        dispatcher=None,
        event_publisher=None,
        publish_authorization_reader=None,
        cancellation_reader=None,
        worker_id: str = "local-worker",
    ) -> None:
        self._journal = journal
        self._dispatcher = dispatcher
        self._event_publisher = event_publisher
        self._publish_authorization_reader = publish_authorization_reader
        self._cancellation_reader = cancellation_reader
        self._worker_id = worker_id

    def bind_internal_response_dispatcher(self, dispatcher) -> None:
        self._dispatcher = dispatcher

    async def publish_from_hub(self, hub_id: str, payload: dict) -> None:
        room_id = payload.get("room_id", "")
        owner_id = payload.get("owner_id", "")
        events = payload.get("events", [])
        for index, item in enumerate(events):
            data = dict(item.get("data", {}))
            event_type = item.get("type")
            if event_type not in SUPPORTED_HUB_PUBLISH_EVENT_TYPES:
                continue
            agent_message_id = item.get("agent_message_id")
            lineage = await self._authorize_event(
                hub_id=hub_id,
                owner_id=owner_id,
                room_id=room_id,
                agent_message_id=agent_message_id,
            )
            if lineage is UNAUTHORIZED_EVENT:
                continue
            if await self._is_cancelled(agent_message_id, lineage):
                continue
            if not _processing_status_lifecycle_matches(event_type, data, lineage):
                continue
            task_id = (
                data.get("task_id")
                or payload.get("task_id")
                or getattr(lineage, "tracked_task_id", None)
                or item.get("agent_message_id", "")
            )
            internal_payload = normalize_hub_publish_payload(
                event_type,
                agent_message_id,
                data,
                task_id=task_id,
                lineage=lineage,
            )
            stable = stable_response_key(hub_id, task_id, data.get("response_seq"))
            journal = None
            idempotency_key = stable or ingest_idempotency_key()
            if self._journal:
                journal_event = {
                    "hub_id": hub_id,
                    "room_id": room_id,
                    "agent_message_id": agent_message_id,
                    "task_id": task_id,
                    "event_type": event_type,
                    "run_id": data.get("run_id"),
                    "payload": internal_payload,
                    "idempotency_key": idempotency_key,
                    "correlation_fingerprint": legacy_correlation_fingerprint(
                        hub_id,
                        room_id,
                        agent_message_id or "",
                        event_type or "",
                        index,
                        data,
                    ),
                }
                if stable:
                    journal_event["stable_idempotency_key"] = stable
                journal = await self._journal.create_or_get(
                    journal_event
                )
                idempotency_key = journal["idempotency_key"]
                if journal.get("processed"):
                    continue
            event = HubAgentResponseInternal(
                timestamp=utcnow(),
                hub_id=hub_id,
                agent_id=internal_payload.get("agent_id", ""),
                task_id=task_id,
                room_id=room_id,
                is_terminal=is_terminal_hub_publish_event(internal_payload, data),
                journal_id=journal.get("journal_id") if journal else None,
                idempotency_key=idempotency_key,
                run_id=internal_payload.get("run_id"),
                payload=internal_payload,
            )
            if self._dispatcher:
                await self._dispatcher.dispatch_hub_internal_response(event)
            if self._event_publisher:
                if self._journal and journal and not journal.get("processed"):
                    claim = await self._journal.claim_for_processing(
                        journal["journal_id"], self._worker_id
                    )
                    if claim is None:
                        continue
                    else:
                        event = event.model_copy(
                            update={"claim_token": claim.get("claim_token")}
                        )
                        await self._event_publisher.emit_internal(event)
                        continue
                await self._event_publisher.emit_internal(event)

    async def _authorize_event(
        self,
        *,
        hub_id: str,
        owner_id: str,
        room_id: str,
        agent_message_id: str | None,
    ):
        if self._publish_authorization_reader is None:
            return None
        if not owner_id or not agent_message_id:
            return UNAUTHORIZED_EVENT
        lineage = await self._publish_authorization_reader.authorize_hub_publish(
            hub_id=hub_id,
            owner_id=owner_id,
            room_id=room_id,
            agent_message_id=agent_message_id,
        )
        if lineage is None:
            return UNAUTHORIZED_EVENT
        return lineage

    async def _is_cancelled(self, agent_message_id: str | None, lineage) -> bool:
        if self._cancellation_reader is None:
            return False
        message_ids = []
        if lineage is not None:
            message_ids.extend(getattr(lineage, "cancellation_message_ids", []) or [])
        if agent_message_id:
            message_ids.append(agent_message_id)
        for message_id in dict.fromkeys(message_ids):
            if await self._cancellation_reader.is_message_cancelled(message_id):
                return True
        return False


def normalize_hub_publish_payload(
    event_type: str | None,
    agent_message_id: str | None,
    data: dict,
    *,
    task_id: str,
    lineage=None,
) -> dict:
    payload = dict(data)
    kind = LEGACY_RELAY_EVENT_KIND_MAP.get(event_type or "", event_type or "")
    payload["kind"] = kind
    payload["legacy_event_type"] = event_type or kind
    payload.setdefault("message_id", agent_message_id or "")
    payload.setdefault("task_id", task_id)
    if lineage is not None:
        payload["agent_id"] = getattr(lineage, "agent_id", "")
        payload["related_message_id"] = getattr(lineage, "related_message_id", None)
        payload["turn_id"] = getattr(lineage, "turn_id", None)
        payload["run_id"] = getattr(lineage, "run_id", None)
        payload["lifecycle_message_id"] = getattr(
            lineage, "lifecycle_message_id", None
        )
        payload["lifecycle_message_id_verified"] = bool(
            getattr(lineage, "lifecycle_message_id", None)
        )

    if event_type == "agent_response":
        payload.setdefault("text", str(payload.get("content") or ""))
        payload.setdefault("content", payload.get("text", ""))
        if isinstance(payload.get("parts"), list):
            payload["parts"] = _normalize_hub_parts(payload.get("parts"))
    elif event_type == "agent_error":
        error_text = payload.get("error_text") or payload.get("error") or "Unknown agent error"
        payload["error_text"] = str(error_text)
        payload.setdefault("error", payload["error_text"])
        payload.setdefault("text", str(error_text))
        payload.setdefault("state", "failed")
    elif event_type == "task_status":
        _normalize_task_status_payload(payload)
    elif event_type == "artifact_update":
        _normalize_artifact_update_payload(payload)
    elif event_type == "task_interactive":
        status = payload.get("state") or payload.get("status") or "input-required"
        payload["state"] = str(status)
        payload.setdefault(
            "text",
            str(payload.get("status_text") or payload.get("prompt") or ""),
        )
    elif event_type == "processing_status":
        status = payload.get("state") or payload.get("status") or "completed"
        payload["state"] = str(status)
        payload["status"] = str(status)

    return payload


def _processing_status_lifecycle_matches(
    event_type: str | None,
    data: dict,
    lineage,
) -> bool:
    if event_type != "processing_status" or lineage is None:
        return True
    supplied = data.get("user_message_id")
    if not supplied:
        return True
    allowed = {
        getattr(lineage, "lifecycle_message_id", None),
        getattr(lineage, "root_user_message_id", None),
        getattr(lineage, "turn_id", None),
    }
    return supplied in {item for item in allowed if item}


def _normalize_artifact_update_payload(payload: dict) -> None:
    if "artifacts" in payload:
        return
    raw = payload.get("raw") if isinstance(payload.get("raw"), dict) else {}
    artifact = raw.get("artifact") or payload.get("artifact")
    if artifact:
        if isinstance(artifact, dict) and artifact.get("parts"):
            artifact = {**artifact, "parts": _normalize_hub_parts(artifact.get("parts"))}
        payload["artifacts"] = [artifact]


def _normalize_hub_parts(parts: list[dict] | None) -> list[dict] | None:
    if not parts:
        return parts
    normalized: list[dict] = []
    seen_file_keys: set[tuple] = set()
    for part in parts:
        if not isinstance(part, dict) or part.get("kind"):
            normalized.append(part)
        elif "text" in part:
            out = {"kind": "text", "text": part.get("text", "")}
            if "metadata" in part:
                out["metadata"] = part["metadata"]
            normalized.append(out)
        elif "raw" in part or "url" in part:
            file_info = {}
            if "raw" in part:
                file_info["bytes"] = part["raw"]
            if "url" in part:
                file_info["uri"] = part["url"]
            media_type = (
                part.get("mime_type") or part.get("mimeType") or part.get("mediaType")
            )
            if media_type:
                file_info["mimeType"] = media_type
            filename = part.get("filename") or part.get("name")
            if filename:
                file_info["name"] = filename
            file_key = (
                file_info.get("bytes"),
                file_info.get("uri"),
                file_info.get("mimeType"),
                file_info.get("name"),
            )
            if file_key in seen_file_keys:
                continue
            seen_file_keys.add(file_key)
            out = {"kind": "file", "file": file_info}
            if "metadata" in part:
                out["metadata"] = part["metadata"]
            normalized.append(out)
        elif "data" in part:
            out = {"kind": "data", "data": part.get("data")}
            if "metadata" in part:
                out["metadata"] = part["metadata"]
            normalized.append(out)
        else:
            normalized.append(part)
    return normalized


def _normalize_task_status_payload(payload: dict) -> None:
    try:
        state = CommonTaskState(payload.get("state") or payload.get("status") or "")
    except ValueError:
        payload["kind"] = "status_update"
        status = payload.get("state") or payload.get("status")
        if status:
            payload["state"] = str(status)
        return

    status_text = str(payload.get("status_text") or payload.get("text") or "")
    payload["state"] = state.value
    if state == CommonTaskState.CANCELED:
        payload["kind"] = "canceled"
        payload.setdefault("text", status_text)
    elif is_terminal_state(state):
        if is_failure_state(state):
            payload["kind"] = "error"
            payload.setdefault("error_text", status_text or f"Agent task {state.value}")
            payload.setdefault("text", payload["error_text"])
        else:
            payload["kind"] = "response"
            payload.setdefault("text", status_text)
    elif state in INTERACTIVE_STATES:
        payload["kind"] = "interactive"
        payload.setdefault("text", status_text)
    else:
        payload["kind"] = "status_update"
        payload.setdefault("text", status_text)


def is_terminal_hub_publish_event(payload: dict, raw_data: dict) -> bool:
    if "is_terminal" in raw_data:
        return bool(raw_data["is_terminal"])
    return payload.get("kind") in TERMINAL_AGENT_EVENT_KINDS


def internal_event_from_journal_claim(claim: dict) -> HubAgentResponseInternal:
    payload = dict(claim.get("payload") or {})
    return HubAgentResponseInternal(
        timestamp=utcnow(),
        hub_id=claim.get("hub_id", ""),
        agent_id=payload.get("agent_id", ""),
        task_id=claim.get("task_id", payload.get("task_id", "")),
        room_id=claim.get("room_id", ""),
        is_terminal=is_terminal_hub_publish_event(payload, payload),
        journal_id=claim.get("journal_id"),
        idempotency_key=claim.get("idempotency_key"),
        run_id=claim.get("run_id"),
        claim_token=claim.get("claim_token"),
        payload=payload,
    )


__all__ = [
    "HubPublishService",
    "internal_event_from_journal_claim",
    "is_terminal_hub_publish_event",
    "normalize_hub_publish_payload",
]
