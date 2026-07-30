"""AgentResponseHandler — single source of truth for processing agent results.

Terminal events delegate to ``notify_task_update`` for SSE emission.
Nonterminal ``artifact_update`` events are folded before durable file
conversion, mutation, or public delivery.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from typing import TYPE_CHECKING, Any, Protocol

from a2a_adapter.task_status import coerce_task_state
from common.a2a_constants import is_interactive_state
from common.a2a_task_projection import public_artifact_data, public_part_data
from common.config.settings import settings
from common.observability import traced_create_task
from common.utils.a2a_helpers import (
    extract_text_from_artifact_dicts,
    filter_non_text_parts,
)
from common.utils.logger import get_logger
from execution.dispatch.agent_event import AgentEvent
from execution.orchestration.result_ingestor import AgentResultRead
from execution.task_tracking import resolve_public_task_label

if TYPE_CHECKING:
    from execution.ports import ExecutionDeliveryPort, TaskNotificationStorePort


class ResponseMessageWriter(Protocol):
    async def accumulate_artifact_on_message(
        self,
        message_id: str,
        artifact: dict,
        *,
        append: bool = False,
        update_key: str | None = None,
    ) -> bool: ...


class ResponseTaskWriter(Protocol):
    async def update_task_state_on_message(
        self,
        message_id: str,
        state: str,
        *,
        message_text: str | None = None,
        artifacts: list[dict] | None = None,
        task_id: str | None = None,
        context_id: str | None = None,
    ) -> tuple[bool, str | None]: ...


class ResponseContinuationStore(Protocol):
    async def get_pending_continuation_on_message(
        self,
        message_id: str,
    ) -> dict | None: ...


class ResponseClientRequestResolver(Protocol):
    async def resolve_client_request_id_for_message_id(
        self, message_id: str
    ) -> str | None: ...

    async def get_room_agent_message_by_message_id(self, message_id: str): ...

    async def resolve_client_request_id_for_agent_message(
        self, room_agent_message
    ) -> str | None: ...


class ResponseRoomReader(Protocol):
    async def get_room_by_room_id(self, room_id: str): ...


class ResponseHITLReader(Protocol):
    async def get_pending_hitl_requests_for_message(
        self, user_message_id: str
    ) -> list[dict]: ...


class OrchestrationResultIngestorService(Protocol):
    async def ingest_agent_result(self, result: AgentResultRead) -> Any: ...


logger = get_logger(__name__)
_orchestration_result_ingestor: OrchestrationResultIngestorService | None = None

_PUBLIC_TERMINAL_ERRORS = {
    "failed": "Task failed",
    "error": "Task failed",
    "rate_limited": "Task failed",
    "rejected": "Task was rejected by the agent",
    "canceled": "Task was canceled",
    "expired": "Task expired",
}
_GENERIC_AGENT_INPUT_PROMPT = "The agent needs additional information."


def _safe_terminal_error(status: str | None) -> str:
    return _PUBLIC_TERMINAL_ERRORS.get(str(status or "failed"), "Task failed")


def _part_payload(part: dict) -> dict:
    root = part.get("root")
    return root if isinstance(root, dict) else part


def _part_has_unaddressable_file(part: dict) -> bool:
    payload = _part_payload(part)
    file_payload = payload.get("file") if isinstance(payload, dict) else None
    return isinstance(file_payload, dict) and not file_payload.get("uri")


def _sanitize_public_parts(parts: list[dict] | None) -> list[dict]:
    sanitized: list[dict] = []
    for part in parts or []:
        if not isinstance(part, dict):
            continue
        public_part = public_part_data(part)
        if public_part is None:
            continue
        if _part_has_unaddressable_file(public_part):
            continue
        sanitized.append(public_part)
    return sanitized


def _sanitize_public_artifacts(
    artifacts: list[dict] | None,
    *,
    keep_empty_artifacts: bool = False,
) -> list[dict]:
    sanitized: list[dict] = []
    for artifact in artifacts or []:
        if not isinstance(artifact, dict):
            continue
        public_artifact = public_artifact_data(artifact)
        public_artifact["parts"] = _sanitize_public_parts(
            public_artifact.get("parts") or []
        )
        if keep_empty_artifacts or public_artifact["parts"]:
            sanitized.append(public_artifact)
    return sanitized


def _materialized_text_artifact(message_id: str, text: str) -> dict:
    return {
        "artifactId": f"{message_id}-response",
        "name": "response",
        "parts": [{"kind": "text", "text": text}],
    }


def _artifact_identity(artifact: dict, position: int) -> str:
    artifact_id = artifact.get("artifactId") or artifact.get("artifact_id")
    if artifact_id:
        return str(artifact_id)
    explicit_index = artifact.get("index")
    if explicit_index is not None:
        return f"index:{explicit_index}"
    return f"slot:{position}"


def _with_durable_artifact_identity(artifact: dict, position: int) -> dict:
    if artifact.get("artifactId") or artifact.get("artifact_id"):
        return artifact
    return {**artifact, "artifactId": _artifact_identity(artifact, position)}


def _artifact_file_ids(artifact: dict) -> set[str]:
    file_ids: set[str] = set()
    for part in artifact.get("parts") or []:
        payload = _part_payload(part)
        metadata = payload.get("metadata") if isinstance(payload, dict) else None
        if not isinstance(metadata, dict):
            continue
        file_id = metadata.get("file_id")
        if isinstance(file_id, str) and file_id:
            file_ids.add(file_id)
    return file_ids


def _superseded_artifact_file_ids(
    existing_artifacts: list[dict],
    artifact: dict,
    position: int,
    *,
    append: bool,
) -> set[str]:
    if append:
        return set()
    identity = _artifact_identity(artifact, position)
    for existing_position, existing in enumerate(existing_artifacts):
        if _artifact_identity(existing, existing_position) == identity:
            return _artifact_file_ids(existing)
    return set()


def _retained_artifacts_for_update(
    existing: list[dict],
    incoming: list[dict],
    *,
    append: bool,
) -> list[dict]:
    if append:
        return existing
    replaced_identities = {
        _artifact_identity(artifact, position)
        for position, artifact in enumerate(incoming)
    }
    return [
        artifact
        for position, artifact in enumerate(existing)
        if _artifact_identity(artifact, position) not in replaced_identities
    ]


def _merge_artifact_journal(
    existing: list[dict],
    incoming: list[dict],
    *,
    append: bool,
) -> list[dict]:
    merged = [dict(artifact) for artifact in existing]
    positions = {
        _artifact_identity(artifact, position): position
        for position, artifact in enumerate(merged)
    }
    for incoming_position, raw_artifact in enumerate(incoming):
        artifact = _with_durable_artifact_identity(raw_artifact, incoming_position)
        identity = _artifact_identity(artifact, incoming_position)
        existing_position = positions.get(identity)
        if existing_position is None:
            positions[identity] = len(merged)
            merged.append(artifact)
            continue
        if append:
            current = merged[existing_position]
            new_parts = list(artifact.get("parts") or [])
            current_parts = list(current.get("parts") or [])
            if new_parts and current_parts[-len(new_parts) :] != new_parts:
                current["parts"] = current_parts + new_parts
            current.update(
                {key: value for key, value in artifact.items() if key != "parts"}
            )
        else:
            merged[existing_position] = artifact
    return merged


def bind_orchestration_result_ingestor(
    service: OrchestrationResultIngestorService | None,
) -> None:
    global _orchestration_result_ingestor
    _orchestration_result_ingestor = service


class AgentResponseHandler:
    """Single source of truth for processing agent results.

    Terminal events delegate to notify_task_update for SSE emission.
    Nonterminal artifact updates are not a public completed-output path.
    """

    def __init__(
        self,
        message_writer: ResponseMessageWriter,
        task_writer: ResponseTaskWriter,
        continuation_store: ResponseContinuationStore,
        client_request_resolver: ResponseClientRequestResolver,
        room_reader: ResponseRoomReader,
        hitl_reader: ResponseHITLReader,
        delivery: ExecutionDeliveryPort,
        room_message_center: object,
        slot_lifecycle=None,
        hitl_coordinator=None,
        task_notifier=None,
        task_notification_store: TaskNotificationStorePort | None = None,
        task_notification_impl=None,
        room_files=None,
    ) -> None:
        self._message_writer = message_writer
        self._task_writer = task_writer
        self._continuation_store = continuation_store
        self._client_request_resolver = client_request_resolver
        self._room_reader = room_reader
        self._hitl_reader = hitl_reader
        self._delivery = delivery
        self._rmc = room_message_center
        self._slot_lifecycle = slot_lifecycle
        self.hitl_coordinator = hitl_coordinator
        self._task_notifier = task_notifier
        if task_notification_impl is not None and task_notification_store is None:
            raise RuntimeError("Task notification store dependency has not been bound")
        self._task_notification_store = task_notification_store
        self._task_notification_impl = task_notification_impl
        self._room_files = room_files
        self._processing_status_emitter = None

    def bind_execution_event_deps(self, processing_status_emitter) -> None:
        self._processing_status_emitter = processing_status_emitter

    async def _resolve_client_request_id(self, e: AgentEvent) -> str | None:
        explicit = e.client_request_id
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip()

        message_id = e.message_id
        if not isinstance(message_id, str) or not message_id.strip():
            return None

        resolve_from_message_id = getattr(
            self._client_request_resolver,
            "resolve_client_request_id_for_message_id",
            None,
        )
        if callable(resolve_from_message_id):
            maybe_resolved = resolve_from_message_id(message_id)
            resolved = (
                await maybe_resolved
                if inspect.isawaitable(maybe_resolved)
                else maybe_resolved
            )
            if isinstance(resolved, str) and resolved.strip():
                return resolved.strip()

        get_agent_message = getattr(
            self._client_request_resolver, "get_room_agent_message_by_message_id", None
        )
        resolve_from_agent_message = getattr(
            self._client_request_resolver,
            "resolve_client_request_id_for_agent_message",
            None,
        )
        if callable(get_agent_message) and callable(resolve_from_agent_message):
            maybe_room_agent_message = get_agent_message(message_id)
            room_agent_message = (
                await maybe_room_agent_message
                if inspect.isawaitable(maybe_room_agent_message)
                else maybe_room_agent_message
            )
            if room_agent_message is None:
                return None
            maybe_resolved = resolve_from_agent_message(room_agent_message)
            resolved = (
                await maybe_resolved
                if inspect.isawaitable(maybe_resolved)
                else maybe_resolved
            )
            if isinstance(resolved, str) and resolved.strip():
                return resolved.strip()

        return None

    async def _terminate_slot(
        self,
        e: AgentEvent,
        status: str,
        content: str | None = None,
        artifacts: list[dict] | None = None,
        error: str | None = None,
        has_partial_content: bool | None = None,
    ) -> None:
        """Emit slot_terminated turn event if slot_lifecycle is available and turn_id is set."""
        if not getattr(self, "_slot_lifecycle", None) or not e.turn_id:
            return
        try:
            await self._slot_lifecycle.terminate_slot(
                room_id=e.room_id,
                turn_id=e.turn_id,
                slot_id=e.message_id,
                status=status,
                content=content,
                artifacts=artifacts,
                error=error,
                has_partial_content=has_partial_content,
            )
        except Exception:
            logger.warning(
                "AgentResponseHandler: slot_terminated emission failed for %s",
                e.message_id,
                exc_info=True,
            )

    async def handle(self, event: AgentEvent) -> None:
        if self._room_files is not None:
            async with self._room_files.write_lease(
                event.room_id, f"agent-event:{event.kind}"
            ):
                await self._handle(event)
            return
        await self._handle(event)

    async def _handle(self, event: AgentEvent) -> None:
        match event.kind:
            case "artifact_update":
                await self._on_artifact(event)
            case "response":
                await self._on_response(event)
            case "error":
                await self._on_error(event)
            case "canceled":
                await self._on_canceled(event)
            case "task_submitted":
                await self._on_submitted(event)
            case "status_update":
                await self._on_status(event)
            case "interactive":
                await self._on_interactive(event)
            case "processing_status":
                await self._on_processing_status(event)

    # --- Nonterminal artifacts (durable journal, no public side effects) ---

    async def _on_artifact(self, e: AgentEvent) -> None:
        await self._run_artifact_materialization(
            e,
            lambda: self._process_artifact(e),
        )

    async def _process_artifact(self, e: AgentEvent) -> None:
        """Materialize and journal artifact updates for terminal reconstruction."""
        artifacts = list(e.artifacts or [])
        if not artifacts and e.parts:
            artifacts = [
                {
                    "artifactId": f"{e.message_id}-stream",
                    "name": "stream",
                    "parts": e.parts,
                }
            ]
        if not artifacts:
            return
        from common.utils.a2a_helpers import materialize_inline_file_parts

        existing_artifacts = await self._existing_artifact_journal(e.message_id)
        budget = self._artifact_budget(
            _retained_artifacts_for_update(
                existing_artifacts,
                artifacts,
                append=e.append,
            )
        )
        for artifact_position, artifact in enumerate(artifacts):
            update_key = (
                hashlib.sha256(
                    json.dumps(
                        {
                            "event_id": e.artifact_update_id,
                            "artifact_position": artifact_position,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest()
                if e.artifact_update_id
                else None
            )
            seen_method = getattr(
                self._message_writer, "is_artifact_update_recorded", None
            )
            method_is_explicit = "is_artifact_update_recorded" in getattr(
                self._message_writer, "__dict__", {}
            ) or hasattr(type(self._message_writer), "is_artifact_update_recorded")
            if (
                method_is_explicit
                and callable(seen_method)
                and update_key is not None
                and await seen_method(e.message_id, update_key)
            ):
                continue
            artifact = _with_durable_artifact_identity(artifact, artifact_position)
            superseded_file_ids = _superseded_artifact_file_ids(
                existing_artifacts,
                artifact,
                artifact_position,
                append=e.append,
            )
            parts = artifact.get("parts")
            if isinstance(parts, list):
                try:
                    artifact_id = artifact.get("artifactId") or artifact.get(
                        "artifact_id"
                    )
                    explicit_index = artifact.get("index")
                    artifact_slot = (
                        f"id:{artifact_id}"
                        if artifact_id
                        else f"index:{explicit_index}"
                        if explicit_index is not None
                        else f"slot:{artifact_position}"
                    )
                    await materialize_inline_file_parts(
                        parts,
                        e.room_id,
                        e.message_id,
                        budget=budget,
                        artifact_slot=artifact_slot,
                    )
                except Exception:
                    logger.warning(
                        "Could not materialize artifact journal files for %s",
                        e.message_id,
                        exc_info=True,
                    )
            public_artifact = public_artifact_data(artifact)
            public_artifact["artifactId"] = _artifact_identity(
                artifact, artifact_position
            )
            if e.last_chunk:
                public_artifact["metadata"] = {"last_chunk": True}
            public_artifact["parts"] = _sanitize_public_parts(
                public_artifact.get("parts") or []
            )
            if not public_artifact["parts"]:
                continue
            persisted = await self._message_writer.accumulate_artifact_on_message(
                e.message_id,
                public_artifact,
                append=e.append,
                update_key=update_key,
            )
            await self._delete_superseded_artifact_files(
                e,
                persisted=persisted,
                previous_file_ids=superseded_file_ids,
                current_artifact=public_artifact,
            )

    async def _delete_superseded_artifact_files(
        self,
        e: AgentEvent,
        *,
        persisted: bool,
        previous_file_ids: set[str],
        current_artifact: dict,
    ) -> None:
        del current_artifact
        committed_artifacts = await self._existing_artifact_journal(e.message_id)
        if not committed_artifacts:
            return
        committed_file_ids = {
            file_id
            for artifact in committed_artifacts
            for file_id in _artifact_file_ids(artifact)
        }
        file_ids = previous_file_ids - committed_file_ids
        if not persisted or not file_ids:
            return
        try:
            from common.utils.a2a_helpers import delete_superseded_agent_artifacts

            await delete_superseded_agent_artifacts(
                room_id=e.room_id,
                message_id=e.message_id,
                file_ids=file_ids,
            )
        except Exception:
            logger.warning(
                "Could not remove superseded artifact files for %s",
                e.message_id,
                exc_info=True,
            )

    async def _run_artifact_materialization(  # noqa: C901
        self, e: AgentEvent, operation
    ) -> None:
        claim = getattr(self._message_writer, "claim_artifact_materialization", None)
        explicit = "claim_artifact_materialization" in getattr(
            self._message_writer, "__dict__", {}
        ) or hasattr(type(self._message_writer), "claim_artifact_materialization")
        if not explicit or not callable(claim):
            await operation()
            return
        token = await claim(
            e.message_id,
            e.artifact_update_id or f"{e.task_id or e.message_id}:artifact",
        )
        if token is None:
            return
        heartbeat = self._message_writer.heartbeat_artifact_materialization
        release = self._message_writer.release_artifact_materialization
        stopped = asyncio.Event()
        owner_task = asyncio.current_task()

        async def maintain() -> None:
            while not stopped.is_set():
                try:
                    await asyncio.wait_for(stopped.wait(), timeout=30)
                    return
                except TimeoutError:
                    try:
                        renewed = await heartbeat(e.message_id, token)
                    except Exception:
                        renewed = False
                    if not renewed:
                        stopped.set()
                        if owner_task is not None:
                            owner_task.cancel()
                        return

        maintainer = traced_create_task(
            maintain(),
            name=f"artifact-lease-{e.message_id}",
        )
        try:
            await operation()
            if stopped.is_set():
                raise RuntimeError(
                    f"Artifact materialization lease lost for {e.message_id}"
                )
        except asyncio.CancelledError:
            if stopped.is_set():
                raise RuntimeError(
                    f"Artifact materialization lease lost for {e.message_id}"
                ) from None
            raise
        finally:
            stopped.set()
            maintainer.cancel()
            await asyncio.gather(maintainer, return_exceptions=True)
            await release(e.message_id, token)

    # --- Terminal events (DB persist -> notify_task_update -> orchestration) ---

    async def _project_completed_output(  # noqa: C901
        self,
        e: AgentEvent,
    ) -> tuple[str | None, list[dict] | None]:
        had_structured_output = bool(e.parts or e.artifacts)
        existing_artifacts = await self._existing_artifact_journal(e.message_id)
        # Materialize inline file parts before public projection.
        if had_structured_output:
            from common.utils.a2a_helpers import materialize_inline_file_parts

            try:
                incoming_for_budget = e.artifacts or [
                    {
                        "artifactId": f"{e.message_id}-response",
                        "parts": e.parts or [],
                    }
                ]
                budget = self._artifact_budget(
                    _retained_artifacts_for_update(
                        existing_artifacts,
                        incoming_for_budget,
                        append=e.append,
                    )
                )
                if e.artifacts:
                    for artifact_position, artifact in enumerate(e.artifacts):
                        artifact = _with_durable_artifact_identity(
                            artifact, artifact_position
                        )
                        e.artifacts[artifact_position] = artifact
                        artifact_id = artifact.get("artifactId") or artifact.get(
                            "artifact_id"
                        )
                        explicit_index = artifact.get("index")
                        artifact_slot = (
                            f"id:{artifact_id}"
                            if artifact_id
                            else f"index:{explicit_index}"
                            if explicit_index is not None
                            else f"slot:{artifact_position}"
                        )
                        artifact_parts = artifact.get("parts", [])
                        if artifact_parts:
                            await materialize_inline_file_parts(
                                artifact_parts,
                                e.room_id,
                                e.message_id,
                                budget=budget,
                                artifact_slot=artifact_slot,
                            )
                elif e.parts:
                    await materialize_inline_file_parts(
                        e.parts,
                        e.room_id,
                        e.message_id,
                        budget=budget,
                        artifact_slot=f"id:{e.message_id}-response",
                    )
            except Exception:
                logger.warning(
                    "File materialization failed for terminal artifacts on message %s; "
                    "dropping unaddressable file bytes before persistence",
                    e.message_id,
                    exc_info=True,
                )

        artifacts_for_db: list[dict] | None = None
        if e.artifacts:
            incoming = _sanitize_public_artifacts(e.artifacts)
            artifacts_for_db = (
                _merge_artifact_journal(
                    existing_artifacts,
                    incoming,
                    append=e.append,
                )
                or None
            )
        elif e.parts:
            sanitized_parts = _sanitize_public_parts(e.parts)
            if sanitized_parts:
                incoming = [
                    {
                        "artifactId": f"{e.message_id}-response",
                        "name": "response",
                        "parts": sanitized_parts,
                    }
                ]
                artifacts_for_db = _merge_artifact_journal(
                    existing_artifacts,
                    incoming,
                    append=e.append,
                )
        elif e.public_text or e.text:
            artifacts_for_db = existing_artifacts + [
                _materialized_text_artifact(e.message_id, e.public_text or e.text)
            ]

        display_text = e.public_text or extract_text_from_artifact_dicts(
            artifacts_for_db
        )
        if (
            e.text
            and not display_text
            and not artifacts_for_db
            and not had_structured_output
        ):
            artifacts_for_db = [_materialized_text_artifact(e.message_id, e.text)]
            display_text = e.text

        display_artifacts = artifacts_for_db
        e.artifacts = artifacts_for_db
        e.parts = filter_non_text_parts(
            [
                part
                for artifact in artifacts_for_db or []
                for part in artifact.get("parts") or []
            ]
        )
        e.text = display_text or ""
        e.error_text = None
        e.details = None
        return display_text, display_artifacts

    async def _artifact_budget_from_journal(self, message_id: str) -> dict[str, Any]:
        return self._artifact_budget(await self._existing_artifact_journal(message_id))

    @staticmethod
    def _artifact_budget(artifacts: list[dict]) -> dict[str, Any]:
        converted = 0
        raw = 0
        precounted_file_ids: dict[str, int] = {}
        for artifact in artifacts:
            for part in artifact.get("parts") or []:
                if part.get("kind") != "file":
                    continue
                metadata = part.get("metadata") or {}
                converted += 1
                raw += int(metadata.get("size_bytes") or 0)
                file_id = metadata.get("file_id")
                if isinstance(file_id, str) and file_id:
                    precounted_file_ids[file_id] = (
                        precounted_file_ids.get(file_id, 0) + 1
                    )
        return {
            "converted": converted,
            "attempted": converted,
            "raw": raw,
            "encoded": 0,
            "precounted_file_ids": precounted_file_ids,
        }

    async def _existing_artifact_journal(self, message_id: str) -> list[dict]:
        read_message = getattr(
            self._client_request_resolver,
            "get_room_agent_message_by_message_id",
            None,
        )
        if not callable(read_message):
            return []
        try:
            maybe_message = read_message(message_id)
            message = (
                await maybe_message
                if inspect.isawaitable(maybe_message)
                else maybe_message
            )
            task = (
                message.message_content.message_task
                if message is not None and message.message_content
                else None
            )
            if not task or not task.artifacts:
                return []
            from common.utils.a2a_helpers import artifacts_to_dicts

            return _sanitize_public_artifacts(artifacts_to_dicts(task.artifacts))
        except Exception:
            logger.warning(
                "Could not read artifact journal for terminal message %s",
                message_id,
                exc_info=True,
            )
            raise

    async def _on_response(self, e: AgentEvent) -> None:  # noqa: C901
        finalization_token, fenced = await self._begin_terminal_finalization(
            e, "completed"
        )
        if fenced and finalization_token is None:
            if await self._terminal_replay_already_applied(e, "completed"):
                return
            self._raise_if_retryable_finalization_conflict(e)
            return
        display_text, display_artifacts = await self._run_with_finalization_heartbeat(
            e.message_id,
            finalization_token,
            lambda: self._project_completed_output(e),
        )
        if not e.skip_persist:
            if fenced:
                persisted = await self._set_terminal_finalization_content(
                    e.message_id,
                    finalization_token,
                    message_text=display_text,
                    artifacts=display_artifacts,
                )
                resolved_text = display_text
            else:
                (
                    finalization_token,
                    persisted,
                    resolved_text,
                ) = await self._claim_terminal_finalization(
                    e,
                    state="completed",
                    message_text=display_text,
                    artifacts=display_artifacts,
                )
            if not persisted:
                logger.debug(
                    "Terminal finalizer already claimed for message %s",
                    e.message_id,
                )
                return
            if resolved_text:
                display_text = resolved_text
        await self._run_finalization_step(
            e.message_id,
            finalization_token,
            "notification",
            lambda: self._notify_terminal_best_effort(
                e,
                coerce_task_state("completed"),
                emit_processing_status=e.emit_processing_status,
            ),
        )
        await self._run_finalization_step(
            e.message_id,
            finalization_token,
            "slot_termination",
            lambda: self._terminate_slot(
                e,
                "completed",
                content=display_text,
                artifacts=display_artifacts,
            ),
        )
        await self._run_finalization_step(
            e.message_id,
            finalization_token,
            "result_ingestion",
            lambda: self._ingest_orchestration_result(
                e,
                status="completed",
                text=display_text,
                artifacts=display_artifacts or [],
            ),
        )
        await self._run_finalization_step(
            e.message_id,
            finalization_token,
            "orchestration_resume",
            lambda: self._resume_orchestration(e.message_id, display_text or ""),
        )
        if finalization_token is not None:
            await self._complete_terminal_finalization(
                e.message_id, finalization_token, "completed"
            )

    async def _on_error(self, e: AgentEvent) -> None:
        source_state = e.state or "failed"
        try:
            coerce_task_state(source_state)
            state = source_state
        except (ValueError, KeyError):
            state = "failed"
        error = _safe_terminal_error(source_state)
        e.text = ""
        e.error_text = error
        e.parts = None
        e.artifacts = None
        finalization_token, fenced = await self._begin_terminal_finalization(e, state)
        if fenced and finalization_token is None:
            if await self._terminal_replay_already_applied(e, state):
                return
            self._raise_if_retryable_finalization_conflict(e)
            return
        if not e.skip_persist:
            if fenced:
                persisted = await self._set_terminal_finalization_content(
                    e.message_id,
                    finalization_token,
                    message_text=error,
                    artifacts=None,
                )
            else:
                (
                    finalization_token,
                    persisted,
                    _,
                ) = await self._claim_terminal_finalization(
                    e,
                    state=state,
                    message_text=error,
                    artifacts=None,
                )
            if not persisted:
                return
        await self._run_finalization_step(
            e.message_id,
            finalization_token,
            "notification",
            lambda: self._notify_terminal_best_effort(
                e,
                coerce_task_state(state),
                error=error,
                emit_processing_status=e.emit_processing_status,
            ),
        )
        await self._run_finalization_step(
            e.message_id,
            finalization_token,
            "slot_termination",
            lambda: self._terminate_slot(
                e,
                "failed",
                content=None,
                error=error,
            ),
        )
        await self._run_finalization_step(
            e.message_id,
            finalization_token,
            "result_ingestion",
            lambda: self._ingest_orchestration_result(
                e,
                status=source_state,
                text=None,
                artifacts=[],
                error=error,
            ),
        )
        await self._run_finalization_step(
            e.message_id,
            finalization_token,
            "orchestration_resume",
            lambda: self._resume_orchestration(e.message_id, "", failed=True),
        )
        if finalization_token is not None:
            await self._complete_terminal_finalization(
                e.message_id, finalization_token, state
            )

    async def _on_canceled(self, e: AgentEvent) -> None:
        canceled_text = _safe_terminal_error("canceled")
        e.text = ""
        e.error_text = canceled_text
        e.parts = None
        e.artifacts = None
        finalization_token, fenced = await self._begin_terminal_finalization(
            e, "canceled"
        )
        if fenced and finalization_token is None:
            if await self._terminal_replay_already_applied(e, "canceled"):
                return
            self._raise_if_retryable_finalization_conflict(e)
            return
        if not e.skip_persist:
            if fenced:
                persisted = await self._set_terminal_finalization_content(
                    e.message_id,
                    finalization_token,
                    message_text=canceled_text,
                    artifacts=None,
                )
            else:
                (
                    finalization_token,
                    persisted,
                    _,
                ) = await self._claim_terminal_finalization(
                    e,
                    state="canceled",
                    message_text=canceled_text,
                    artifacts=None,
                )
            if not persisted:
                return
        await self._run_finalization_step(
            e.message_id,
            finalization_token,
            "notification",
            lambda: self._notify_terminal_best_effort(
                e,
                coerce_task_state("canceled"),
                emit_processing_status=e.emit_processing_status,
            ),
        )
        await self._run_finalization_step(
            e.message_id,
            finalization_token,
            "slot_termination",
            lambda: self._terminate_slot(e, "canceled"),
        )
        await self._run_finalization_step(
            e.message_id,
            finalization_token,
            "result_ingestion",
            lambda: self._ingest_orchestration_result(
                e,
                status="canceled",
                text=None,
                artifacts=[],
                error=canceled_text,
            ),
        )
        await self._run_finalization_step(
            e.message_id,
            finalization_token,
            "orchestration_resume",
            lambda: self._resume_orchestration(e.message_id, "", failed=True),
        )
        if finalization_token is not None:
            await self._complete_terminal_finalization(
                e.message_id, finalization_token, "canceled"
            )

    async def _claim_terminal_finalization(
        self,
        e: AgentEvent,
        *,
        state: str,
        message_text: str | None,
        artifacts: list[dict] | None,
    ) -> tuple[str | None, bool, str | None]:
        claim = getattr(self._task_writer, "claim_terminal_finalization", None)
        if callable(claim) and inspect.iscoroutinefunction(claim):
            token, resolved_text = await claim(
                e.message_id,
                state,
                message_text=message_text,
                artifacts=artifacts,
            )
            return token, token is not None, resolved_text
        kwargs: dict[str, Any] = {"message_text": message_text}
        if artifacts is not None or state == "completed":
            kwargs["artifacts"] = artifacts
        update_result = self._task_writer.update_task_state_on_message(
            e.message_id, state, **kwargs
        )
        if not inspect.isawaitable(update_result):
            return None, False, message_text
        persisted, resolved_text = await update_result
        return None, persisted, resolved_text

    async def _begin_terminal_finalization(
        self, e: AgentEvent, state: str
    ) -> tuple[str | None, bool]:
        if e.skip_persist:
            return None, False
        begin = getattr(self._task_writer, "begin_terminal_finalization", None)
        explicit = "begin_terminal_finalization" in getattr(
            self._task_writer, "__dict__", {}
        ) or hasattr(type(self._task_writer), "begin_terminal_finalization")
        if not explicit or not callable(begin):
            return None, False
        return (
            await begin(
                e.message_id,
                state,
                recovery_source=(
                    "journal" if e.retry_on_finalization_conflict else "message"
                ),
                recovery_id=e.finalization_recovery_id,
            ),
            True,
        )

    async def _terminal_replay_already_applied(self, e: AgentEvent, state: str) -> bool:
        if not e.retry_on_finalization_conflict:
            return False
        matches = getattr(self._task_writer, "terminal_finalization_matches", None)
        if not callable(matches):
            return False
        return bool(
            await matches(
                e.message_id,
                state,
                recovery_source="journal",
                recovery_id=e.finalization_recovery_id,
            )
        )

    @staticmethod
    def _raise_if_retryable_finalization_conflict(e: AgentEvent) -> None:
        if e.retry_on_finalization_conflict:
            raise RuntimeError(
                f"Terminal finalization is already in progress for {e.message_id}"
            )

    async def _set_terminal_finalization_content(
        self,
        message_id: str,
        token: str | None,
        *,
        message_text: str | None,
        artifacts: list[dict] | None,
    ) -> bool:
        if token is None:
            return False
        persist = getattr(self._task_writer, "set_terminal_finalization_content", None)
        if not callable(persist):
            return False
        return await persist(
            message_id,
            token,
            message_text=message_text,
            artifacts=artifacts,
        )

    async def _complete_terminal_finalization(
        self, message_id: str, token: str, state: str
    ) -> None:
        complete = getattr(self._task_writer, "complete_terminal_finalization", None)
        if (
            not callable(complete)
            or not inspect.iscoroutinefunction(complete)
            or not await complete(message_id, token, state)
        ):
            raise RuntimeError(
                f"Terminal finalization lease lost for message {message_id}"
            )

    async def _run_finalization_step(  # noqa: C901
        self,
        message_id: str,
        token: str | None,
        step: str,
        operation,
    ) -> None:
        if token is None:
            await operation()
            return
        heartbeat = getattr(self._task_writer, "heartbeat_terminal_finalization", None)
        claim = getattr(self._task_writer, "claim_terminal_finalization_step", None)
        complete = getattr(
            self._task_writer, "complete_terminal_finalization_step", None
        )
        if callable(heartbeat) and not await heartbeat(message_id, token):
            raise RuntimeError(
                f"Terminal finalization lease lost for message {message_id}"
            )
        if callable(claim) and not await claim(message_id, token, step):
            return
        stopped = asyncio.Event()
        owner_task = asyncio.current_task()

        async def maintain() -> None:
            if not callable(heartbeat):
                return
            while not stopped.is_set():
                try:
                    await asyncio.wait_for(stopped.wait(), timeout=60)
                    return
                except TimeoutError:
                    try:
                        renewed = await heartbeat(message_id, token)
                    except Exception:
                        renewed = False
                    if not renewed:
                        stopped.set()
                        if owner_task is not None:
                            owner_task.cancel()
                        return

        maintainer = traced_create_task(
            maintain(),
            name=f"terminal-lease-{message_id}",
        )
        try:
            await operation()
            if stopped.is_set():
                raise RuntimeError(
                    f"Terminal finalization lease lost for message {message_id}"
                )
        except asyncio.CancelledError:
            if stopped.is_set():
                raise RuntimeError(
                    f"Terminal finalization lease lost for message {message_id}"
                ) from None
            raise
        finally:
            stopped.set()
            maintainer.cancel()
            await asyncio.gather(maintainer, return_exceptions=True)
        if callable(complete) and not await complete(message_id, token, step):
            raise RuntimeError(
                f"Terminal finalization step {step} lost for message {message_id}"
            )

    async def _run_with_finalization_heartbeat(  # noqa: C901
        self, message_id: str, token: str | None, operation
    ):
        if token is None:
            return await operation()
        heartbeat = getattr(self._task_writer, "heartbeat_terminal_finalization", None)
        if not callable(heartbeat):
            return await operation()
        stopped = asyncio.Event()
        owner_task = asyncio.current_task()

        async def maintain() -> None:
            while not stopped.is_set():
                try:
                    await asyncio.wait_for(stopped.wait(), timeout=60)
                    return
                except TimeoutError:
                    try:
                        renewed = await heartbeat(message_id, token)
                    except Exception:
                        renewed = False
                    if not renewed:
                        stopped.set()
                        if owner_task is not None:
                            owner_task.cancel()
                        return

        maintainer = traced_create_task(
            maintain(),
            name=f"terminal-lease-{message_id}",
        )
        try:
            result = await operation()
            if stopped.is_set():
                raise RuntimeError(
                    f"Terminal finalization lease lost for message {message_id}"
                )
            return result
        except asyncio.CancelledError:
            if stopped.is_set():
                raise RuntimeError(
                    f"Terminal finalization lease lost for message {message_id}"
                ) from None
            raise
        finally:
            stopped.set()
            maintainer.cancel()
            await asyncio.gather(maintainer, return_exceptions=True)

    async def _on_interactive(self, e: AgentEvent) -> None:
        state = e.state or "input-required"
        prompt = _GENERIC_AGENT_INPUT_PROMPT
        e.text = ""
        e.details = None
        e.parts = None
        e.artifacts = None
        if not e.skip_persist:
            await self._task_writer.update_task_state_on_message(
                e.message_id,
                state,
                message_text=None,
                task_id=e.task_id,
                context_id=e.context_id,
            )

        # For async transports (relay, webhook) the queue has already moved
        # to PAUSED before this callback fires, so QueueExecutor never sees
        # AWAITING_INPUT.  Create the HITL request here when a continuation
        # is already saved (indicates an async callback, not inline dispatch).
        if is_interactive_state(state):
            await self._maybe_create_hitl_for_async_interactive(e, prompt=prompt)

        await self._notify(
            e,
            coerce_task_state(state),
            emit_processing_status=not is_interactive_state(state),
        )

    async def _maybe_create_hitl_for_async_interactive(
        self,
        e: AgentEvent,
        *,
        prompt: str | None = None,
    ) -> None:
        """Create HITL request for async transports (relay / webhook).

        Only acts when a pending_continuation already exists on the message,
        which proves this is an async callback — not an inline direct dispatch
        where QueueExecutor handles HITL creation itself.
        """
        continuation = (
            await self._continuation_store.get_pending_continuation_on_message(
                e.message_id
            )
        )
        if not continuation:
            return

        if self.hitl_coordinator is None:
            raise RuntimeError("HITL coordinator has not been bound")

        user_message_id = continuation.get("user_message_id", "")
        if not user_message_id:
            logger.warning(
                "_maybe_create_hitl_for_async_interactive: no user_message_id "
                "in continuation for %s",
                e.message_id,
            )
            return

        msg = await self._client_request_resolver.get_room_agent_message_by_message_id(
            e.message_id
        )
        agent_name = e.agent_name
        if not agent_name and msg:
            try:
                room = await self._room_reader.get_room_by_room_id(e.room_id)
                if room and room.room_agent_set:
                    agent_name = room.room_agent_set.get(e.agent_id)
            except Exception:
                logger.debug("agent name lookup failed", exc_info=True)

        hitl_req = await self.hitl_coordinator.request_input(
            room_id=e.room_id,
            user_message_id=user_message_id,
            source="agent",
            prompt=prompt or _GENERIC_AGENT_INPUT_PROMPT,
            agent_id=e.agent_id,
            agent_name=agent_name,
            a2a_task_id=e.task_id,
            a2a_context_id=e.context_id,
            continuation_message_id=e.message_id,
            display_message_id=msg.message_id if msg else e.message_id,
        )
        if hitl_req:
            await self._emit_processing_status(
                room_id=e.room_id,
                status="awaiting_input",
                message_id=user_message_id,
                lifecycle_message_id=user_message_id,
            )
            logger.info(
                "Created HITL request %s for async interactive event on %s",
                hitl_req.request_id,
                e.message_id,
            )

    async def _on_submitted(self, e: AgentEvent) -> None:
        client_request_id = await self._resolve_client_request_id(e)
        task_content = await self._resolve_submitted_task_content(e)
        kw: dict = {}
        if client_request_id:
            kw["client_request_id"] = client_request_id
        await self._delivery.send_task_submitted(
            room_id=e.room_id,
            message_id=e.message_id,
            task_id=e.task_id,
            agent_name=e.agent_name,
            agent_id=e.agent_id,
            status="working",
            related_message_id=e.related_message_id,
            created_at=e.created_at,
            step_number=e.step_number,
            total_steps=e.total_steps,
            task_content=task_content,
            **kw,
        )

    async def _resolve_submitted_task_content(self, e: AgentEvent) -> str:
        msg = None
        read_message = getattr(
            self._client_request_resolver,
            "get_room_agent_message_by_message_id",
            None,
        )
        if callable(read_message):
            try:
                maybe_msg = read_message(e.message_id)
                msg = await maybe_msg if inspect.isawaitable(maybe_msg) else maybe_msg
            except Exception:
                logger.debug(
                    "AgentResponseHandler: task label message lookup failed for %s",
                    e.message_id,
                    exc_info=True,
                )

        agent_name = e.agent_name.strip() if isinstance(e.agent_name, str) else ""
        if not agent_name and msg is not None:
            msg_agent_id = getattr(msg, "agent_id", None)
            if isinstance(msg_agent_id, str):
                agent_name = msg_agent_id.strip()
        if not agent_name:
            agent_name = e.agent_id or "agent"

        extend_info = getattr(msg, "extend_info", None) if msg is not None else None
        return resolve_public_task_label(extend_info, agent_name)

    async def _on_status(self, e: AgentEvent) -> None:
        e.text = ""
        e.parts = None
        e.artifacts = None

    async def _on_processing_status(self, e: AgentEvent) -> None:
        if not e.lifecycle_message_id:
            logger.warning(
                "AgentResponseHandler: dropping processing_status without "
                "lifecycle_message_id for %s",
                e.message_id,
            )
            return

        # Status-only terminal callbacks can arrive without a response/error event.
        # In that case neither _on_response nor _on_error ever runs, so close the
        # task before any persistence, notification, lifecycle, or orchestration
        # ingestion side effects run.
        terminal_result_status, terminal_task_state = self._processing_terminal_status(
            e
        )
        emit_details = None
        if terminal_result_status is not None:
            emit_details = await self._close_processing_status_terminal(
                e,
                terminal_result_status=terminal_result_status,
                terminal_task_state=terminal_task_state,
            )

        await self._emit_processing_status(
            room_id=e.room_id,
            status=e.state,
            message_id=e.message_id,
            lifecycle_message_id=e.lifecycle_message_id,
            record_lifecycle=True,
            client_request_id=await self._resolve_client_request_id(e),
            details=emit_details,
        )

    def _processing_terminal_status(self, e: AgentEvent) -> tuple[str | None, Any]:
        if not e.state:
            return None, None

        terminal_result_status: str | None = None
        terminal_task_state = None
        state_value = str(getattr(e.state, "value", e.state))
        if state_value in settings.terminal_processing_statuses:
            terminal_result_status = state_value
        try:
            from common.a2a_constants import TERMINAL_STATES

            state_enum = coerce_task_state(e.state)
            if state_enum in TERMINAL_STATES:
                terminal_result_status = getattr(
                    state_enum,
                    "value",
                    str(state_enum),
                )
                terminal_task_state = state_enum
        except (ValueError, KeyError):
            pass
        except Exception:
            logger.warning(
                "_on_processing_status: terminal close-out failed for %s",
                e.message_id,
                exc_info=True,
            )
        return terminal_result_status, terminal_task_state

    async def _close_processing_status_terminal(
        self,
        e: AgentEvent,
        *,
        terminal_result_status: str,
        terminal_task_state,
    ) -> str | None:
        error = self._terminal_result_error(e, terminal_result_status)
        if error:
            await self._close_processing_status_error(
                e,
                terminal_result_status=terminal_result_status,
                terminal_task_state=terminal_task_state,
                error=error,
            )
            return error

        await self._close_processing_status_completed(
            e,
            terminal_result_status=terminal_result_status,
            terminal_task_state=terminal_task_state,
        )
        return None

    async def _persist_processing_status_terminal(
        self,
        e: AgentEvent,
        *,
        status: str,
        message_text: str | None,
        artifacts: list[dict] | None,
    ) -> str | None:
        if e.skip_persist:
            return None
        try:
            _, resolved_text = await self._task_writer.update_task_state_on_message(
                e.message_id,
                status,
                message_text=message_text,
                artifacts=artifacts,
            )
            return resolved_text
        except Exception:
            logger.warning(
                "_on_processing_status: update_task_state_on_message failed for %s",
                e.message_id,
                exc_info=True,
            )
            return None

    async def _close_processing_status_error(
        self,
        e: AgentEvent,
        *,
        terminal_result_status: str,
        terminal_task_state,
        error: str,
    ) -> None:
        e.text = ""
        e.error_text = error
        e.parts = None
        e.artifacts = None
        e.details = None
        if terminal_task_state is None:
            e.state = terminal_result_status
        e.emit_processing_status = False
        if terminal_result_status == "canceled":
            await self._on_canceled(e)
        else:
            await self._on_error(e)

    async def _close_processing_status_completed(
        self,
        e: AgentEvent,
        *,
        terminal_result_status: str,
        terminal_task_state,
    ) -> None:
        del terminal_result_status
        if terminal_task_state is None:
            return
        e.emit_processing_status = False
        await self._on_response(e)

    # --- Helpers ---

    async def _emit_processing_status(
        self,
        *,
        room_id: str,
        status,
        message_id: str | None,
        lifecycle_message_id: str | None = None,
        record_lifecycle: bool = True,
        client_request_id: str | None = None,
        details=None,
    ) -> None:
        if self._processing_status_emitter is None:
            raise RuntimeError(
                "AgentResponseHandler execution event dependencies not bound"
            )
        status_value = status.value if hasattr(status, "value") else str(status)
        await self._processing_status_emitter(
            room_id=room_id,
            status=status,
            message_id=message_id,
            lifecycle_message_id=lifecycle_message_id,
            record_lifecycle=record_lifecycle,
            client_request_id=client_request_id,
            details=(
                details
                if isinstance(details, dict)
                else {"message": details}
                if isinstance(details, str)
                else None
            ),
            error_message=(
                details
                if isinstance(details, str)
                and status_value in {"failed", "canceled", "rejected", "error"}
                else None
            ),
        )

    async def notify_task_update(
        self,
        message_id: str,
        state: Any,
        room_id: str,
        user_id: str,
        error: str | None = None,
        parts: list[dict] | None = None,
        emit_processing_status: bool = True,
    ) -> bool:
        """Handler-owned task notification — delegates to shared impl.

        Preferred over the standalone ``notify_task_update`` function
        because it uses injected services instead of global singletons.
        """
        if self._task_notifier is None or self._task_notification_impl is None:
            raise RuntimeError(
                "Task notification runtime dependencies have not been bound"
            )
        if self._task_notification_store is None:
            raise RuntimeError("Task notification store dependency has not been bound")

        return await self._task_notification_impl(
            self._task_notification_store,
            self._task_notifier,
            self._delivery,
            message_id=message_id,
            state=state,
            room_id=room_id,
            user_id=user_id,
            error=error,
            parts=parts,
            emit_processing_status=emit_processing_status,
            processing_status_emitter=self._processing_status_emitter,
        )

    async def _notify(
        self,
        e: AgentEvent,
        state: Any,
        error: str | None = None,
        emit_processing_status: bool = True,
    ) -> None:
        await self.notify_task_update(
            message_id=e.message_id,
            state=state,
            room_id=e.room_id,
            user_id=e.user_id or "",
            error=error,
            parts=e.parts,
            emit_processing_status=emit_processing_status,
        )

    async def _notify_terminal_best_effort(
        self,
        e: AgentEvent,
        state: Any,
        *,
        error: str | None = None,
        emit_processing_status: bool = True,
    ) -> None:
        try:
            if emit_processing_status:
                await self._notify(e, state, error=error)
            else:
                await self.notify_task_update(
                    message_id=e.message_id,
                    state=state,
                    room_id=e.room_id,
                    user_id=e.user_id or "",
                    error=error,
                    parts=e.parts,
                    emit_processing_status=False,
                )
        except Exception:
            logger.warning(
                "AgentResponseHandler: terminal task notification failed for %s",
                e.message_id,
                exc_info=True,
            )

    async def _notify_terminal(
        self,
        e: AgentEvent,
        state: Any,
        *,
        error: str | None = None,
        emit_processing_status: bool = True,
    ) -> None:
        if emit_processing_status:
            await self._notify(e, state, error=error)
            return
        await self.notify_task_update(
            message_id=e.message_id,
            state=state,
            room_id=e.room_id,
            user_id=e.user_id or "",
            error=error,
            parts=e.parts,
            emit_processing_status=False,
        )

    @staticmethod
    def _orchestration_result_artifacts(
        e: AgentEvent,
        *,
        persisted_artifacts: list[dict] | None = None,
    ) -> list[dict]:
        if e.artifacts:
            return e.artifacts
        if e.parts:
            non_text = [p for p in e.parts if p.get("kind") in ("file", "data")]
            if non_text:
                return [{"name": "agent-output", "parts": non_text}]
        return persisted_artifacts or []

    @staticmethod
    def _terminal_result_error(e: AgentEvent, status: str) -> str | None:
        if status not in {"failed", "canceled", "rejected", "error", "rate_limited"}:
            return None
        return _safe_terminal_error(status)

    async def _ingest_orchestration_result(
        self,
        e: AgentEvent,
        *,
        status: str,
        text: str | None = None,
        artifacts: list[dict] | None = None,
        error: str | None = None,
    ) -> None:
        service = _orchestration_result_ingestor
        if service is None:
            return

        try:
            result = AgentResultRead(
                agent_message_id=e.message_id,
                agent_id=e.agent_id,
                status=status,
                text=text,
                artifacts=artifacts or [],
                error=error,
            )
            maybe_result = service.ingest_agent_result(result)
            if inspect.isawaitable(maybe_result):
                await maybe_result
        except Exception:
            logger.warning(
                "AgentResponseHandler: orchestration result ingestion failed for %s",
                e.message_id,
                exc_info=True,
            )

    async def _resume_orchestration(
        self,
        message_id: str,
        response_text: str,
        *,
        failed: bool = False,
    ) -> None:
        try:
            await self._rmc.resume_queue_from_continuation(
                message_id=message_id,
                task_result_text=response_text if not failed else None,
                failed=failed,
            )
        except Exception:
            logger.exception("Failed to resume orchestration for %s", message_id)
