"""Dual-routing seam keyed by persisted runtime ownership.

This module is the *only* orchestrator surface the legacy product entry points
(``execution/facade.py`` and ``api_gateway/routes``) are allowed to import. It
owns two concerns:

* **Run-creation routing** — the deterministic, flag-driven decision that
  selects ``orchestrator`` or ``legacy`` for a new user message. The decision
  is made exactly once, at Run creation, and is never re-evaluated for an
  existing Run.
* **Persisted-ownership ingress routing** — webhook observations, HITL answers,
  and cancellation are dispatched back to the runtime that originally owns the
  correlated Run/call/interaction.

The heavy translation between the legacy ``OrchestrationRequest`` envelope and
the orchestrator's ``RoomSessionHost`` inputs also lives here (``process_room_user_message``),
so ``execution/facade.py`` stays orchestrator-import-free.

Mixed-runtime concurrency note: partial per-profile ratios can interleave
legacy and orchestrator Runs inside one Room. Each engine owns an
independent Room lock/claim, so two concurrently-arriving messages in the
same Room may execute in different runtimes without coordinating. The
canary plan must adopt Room-level stickiness (one owner per Room while
mixed) before raising per-profile ratios; this seam documents the risk but
does not implement stickiness.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from common.dto.hitl import (
    A2AInteractionSpec,
    HITLAnswerKind,
    HITLConfirmationAnswer,
    HITLMultiChoiceAnswer,
    HITLQuestionAnswer,
    HITLSingleChoiceAnswer,
    HITLTextAnswer,
)
from common.utils.logger import get_logger
from execution.hitl.exceptions import HITLRoomMismatchError
from execution.orchestrator.a2a_runtime.models import NormalizedA2AObservation
from execution.orchestrator.models import (
    AuthorizationBasis,
    CandidateScopeSnapshot,
    PreparedResourceRef,
    RunResourceManifestSnapshot,
    TextPart,
    UserMessage,
)
from execution.orchestrator.session import (
    DefaultRunFactory,
    RunFactory,
    SessionConflict,
)
from models.request import OrchestrationRequest
from models.response import OrchestrationResponse

logger = get_logger(__name__)

OWNER_ORCHESTRATOR = "orchestrator"
OWNER_LEGACY = "legacy"

MODE_PROFILE_MAP = {
    "fast": "fast",
    "direct": "fast",
    "ultimate": "ultimate",
    "supervisor": "ultimate",
}

# The closed agent-scope enumeration shared with the API boundary
# (api_gateway/routes/room_routes.py) and the frontend AgentScopeInput. A
# scope outside this set means the seam cannot serve the request and the
# legacy executor must take it.
_SERVABLE_SCOPE_SOURCES = frozenset(
    {"mention", "room_default", "all_agents", "saved_group"}
)

_PROFILE_PINNED_INITIAL_ROUTING = "explicit_agent_first"
_PROFILE_PINNED_FINALIZATION = "pass_through"


class UnsupportedEnvelopeError(ValueError):
    """The legacy envelope requests something the orchestrator cannot serve yet."""


class OrchestratorRoutingError(RuntimeError):
    """The routing seam is misconfigured or a required binding is missing."""


class WebhookAuthenticationError(RuntimeError):
    """Webhook auth failure carrying the HTTP status the legacy route would use."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class AttachmentEnvelope:
    file_id: str
    mime_type: str | None = None
    size_bytes: int = 0
    content_digest: str = ""


@dataclass(frozen=True, slots=True)
class RoomMessageEnvelope:
    message_text: str
    mode: str
    candidate_agent_ids: list[str]
    attachments: list[AttachmentEnvelope] | None = None
    # Extracted attachment text (PDF/text projections) rendered into the
    # kernel's user message so the LLM can carry attachment facts into agent
    # tasks instead of losing them during task decomposition.
    attachment_texts: list[str] = field(default_factory=list)
    requesting_subject_id: str | None = None
    # The canonical scope source the candidates were resolved from; it feeds
    # the Run's frozen AuthorizationBasis (membership vs all-active-agents).
    scope_source: str = "explicit_selection"
    group_id: str | None = None


class RoomEnvelopeSource(Protocol):
    async def load_envelope(
        self, request: OrchestrationRequest
    ) -> RoomMessageEnvelope: ...


class WebhookTokenVerifier(Protocol):
    """Legacy ``verify_webhook_token_for_task``-shaped verifier."""

    async def __call__(self, message_id: str, token: str) -> tuple[bool, str]: ...


class RoomMessageEnvelopeResolver:
    """Resolve the orchestrator inputs from the persisted legacy user message.

    ``agent_scope``/``execution_mode`` are written by ``ExecutionFacade`` into
    the user message ``extend_info`` before orchestration is scheduled, so this
    reader reconstructs the mode and candidate scope without importing the
    legacy executor.
    """

    def __init__(
        self,
        *,
        get_user_message: Callable[[str], Awaitable[Any | None]],
        list_room_agent_ids: Callable[[str], Awaitable[list[str]]],
        list_group_agent_ids: Callable[[str], Awaitable[list[str]]] | None = None,
        list_all_active_agent_ids: Callable[[str | None], Awaitable[list[str]]]
        | None = None,
        attachment_text_reader: (
            Callable[[AttachmentEnvelope], Awaitable[str | None]] | None
        ) = None,
    ) -> None:
        self._get_user_message = get_user_message
        self._list_room_agent_ids = list_room_agent_ids
        self._list_group_agent_ids = list_group_agent_ids
        self._list_all_active_agent_ids = list_all_active_agent_ids
        self._attachment_text_reader = attachment_text_reader

    async def load_envelope(self, request: OrchestrationRequest) -> RoomMessageEnvelope:
        message_id = request.room_user_message_id
        if not message_id:
            raise UnsupportedEnvelopeError(
                "orchestrator requires a room_user_message_id"
            )
        message = await self._get_user_message(message_id)
        if message is None:
            raise UnsupportedEnvelopeError(
                f"orchestrator cannot resolve user message {message_id!r}"
            )
        content = getattr(message, "message_content", None)
        message_text = getattr(content, "message_text", None)
        if not isinstance(message_text, str) or not message_text.strip():
            raise UnsupportedEnvelopeError(
                "orchestrator requires a non-empty user message"
            )

        extend_info = getattr(message, "extend_info", None)
        # The live request carries the route-validated mode and scope; they
        # are authoritative for Run creation. The persisted extend_info is the
        # fallback for recovery/re-entry paths without a live request (and for
        # the legacy supervisor preflight's whitelist rewrite).
        live_mode = getattr(request, "mode", None)
        mode = (
            live_mode
            if isinstance(live_mode, str) and live_mode.strip()
            else (
                extend_info.get("execution_mode")
                if isinstance(extend_info, dict)
                else None
            )
        )
        if not isinstance(mode, str) or not mode.strip():
            raise UnsupportedEnvelopeError(
                "orchestrator envelope is missing execution_mode"
            )

        live_scope = getattr(request, "agent_scope", None)
        scope = (
            live_scope
            if isinstance(live_scope, dict)
            else (
                extend_info.get("agent_scope")
                if isinstance(extend_info, dict)
                else None
            )
        )
        if not isinstance(scope, dict):
            # The legacy supervisor preflight whitelists extend_info keys and
            # persists the candidate scope under its own names, so
            # supervisor-mode messages never carry ``agent_scope``.
            # Reconstruct the canonical scope from those fields.
            source = (
                extend_info.get("candidate_scope_source")
                if isinstance(extend_info, dict)
                else None
            )
            agent_ids = (
                extend_info.get("candidate_agent_ids")
                if isinstance(extend_info, dict)
                else None
            )
            if source in {"mention", "saved_group", "all_agents", "room_default"}:
                scope = {"source": source}
                if source == "mention" and isinstance(agent_ids, list):
                    scope["agent_ids"] = [
                        str(agent_id) for agent_id in agent_ids if agent_id
                    ]
                if source == "saved_group":
                    group_id = extend_info.get("candidate_scope_group_id")
                    if isinstance(group_id, str) and group_id.strip():
                        scope["group_id"] = group_id.strip()
        candidate_agent_ids = await self._resolve_candidate_agent_ids(
            request.room_id, scope, user_id=request.user_id
        )
        scope_source = str(scope.get("source") or "explicit_selection")
        group_id = (
            scope.get("group_id") if isinstance(scope.get("group_id"), str) else None
        )

        attachments = _attachments_from_message(content)
        attachment_texts = await self._resolve_attachment_texts(attachments)
        requesting_subject_id = request.user_id
        return RoomMessageEnvelope(
            message_text=message_text,
            mode=mode,
            candidate_agent_ids=candidate_agent_ids,
            attachments=attachments,
            attachment_texts=attachment_texts,
            requesting_subject_id=requesting_subject_id,
            scope_source=scope_source,
            group_id=group_id,
        )

    async def _resolve_attachment_texts(
        self, attachments: list[AttachmentEnvelope]
    ) -> list[str]:
        """Project attachment contents into the kernel's user message."""
        if self._attachment_text_reader is None:
            return []
        blocks: list[str] = []
        for attachment in attachments:
            text = await self._attachment_text_reader(attachment)
            if text:
                blocks.append(
                    f"[attachment {attachment.file_id}"
                    f" ({attachment.mime_type or 'unknown'})]:\n{text}"
                )
        return blocks

    async def _resolve_candidate_agent_ids(
        self, room_id: str | None, scope: Any, *, user_id: str | None = None
    ) -> list[str]:
        if not isinstance(scope, dict):
            raise UnsupportedEnvelopeError(
                "orchestrator envelope is missing agent_scope"
            )
        source = scope.get("source")
        if source == "mention":
            agent_ids = scope.get("agent_ids") or []
            return [str(agent_id) for agent_id in agent_ids if agent_id]
        if source == "room_default":
            if room_id is None:
                raise UnsupportedEnvelopeError(
                    "orchestrator room scope requires room_id"
                )
            return await self._list_room_agent_ids(room_id)
        if source == "all_agents":
            if self._list_all_active_agent_ids is None:
                raise UnsupportedEnvelopeError(
                    "orchestrator all_agents scope is not bound"
                )
            return await self._list_all_active_agent_ids(user_id)
        if source == "saved_group":
            if self._list_group_agent_ids is None:
                raise UnsupportedEnvelopeError(
                    "orchestrator saved_group scope is not bound"
                )
            group_id = scope.get("group_id")
            if not isinstance(group_id, str) or not group_id.strip():
                raise UnsupportedEnvelopeError(
                    "orchestrator saved_group scope requires group_id"
                )
            return await self._list_group_agent_ids(group_id)
        raise UnsupportedEnvelopeError(
            f"orchestrator cannot serve agent scope {source!r}"
        )


def _attachments_from_message(content: Any) -> list[AttachmentEnvelope]:
    raw_attachments = getattr(content, "attachments", None)
    if not isinstance(raw_attachments, list):
        return []
    attachments: list[AttachmentEnvelope] = []
    for item in raw_attachments:
        file_id = (
            item.get("file_id")
            if isinstance(item, dict)
            else getattr(item, "file_id", None)
        )
        if not isinstance(file_id, str) or not file_id.strip():
            continue
        attachments.append(
            AttachmentEnvelope(
                file_id=file_id,
                mime_type=(
                    item.get("mime_type")
                    if isinstance(item, dict)
                    else getattr(item, "mime_type", None)
                ),
                size_bytes=(
                    item.get("size_bytes", 0)
                    if isinstance(item, dict)
                    else getattr(item, "size_bytes", 0)
                )
                or 0,
                content_digest=(
                    item.get("sha256", "")
                    if isinstance(item, dict)
                    else getattr(item, "sha256", "")
                )
                or "",
            )
        )
    return attachments


def stable_route_bucket(room_id: str, client_request_id: str | None) -> int:
    """Deterministic 0-99 bucket shared by every replica for the same request."""
    key = f"{room_id or ''}:{client_request_id or ''}"
    digest = hashlib.sha256(key.encode()).hexdigest()
    return int(digest[:8], 16) % 100


def map_mode_to_profile(mode: str) -> str:
    profile_id = MODE_PROFILE_MAP.get(mode)
    if profile_id is None:
        raise UnsupportedEnvelopeError(f"unsupported execution mode {mode!r}")
    return profile_id


def _allowlist(values: Any) -> set[str]:
    if values is None:
        return set()
    if isinstance(values, str):
        return {values}
    return {str(item) for item in values if item}


# Scope source → AuthorizationBasis.kind. Scopes outside this map (and the
# closed API enumeration) fall back to explicit_selection, which still
# requires room membership like every non-all_agents kind.
_SCOPE_AUTHORIZATION_KINDS = {
    "mention": "mention",
    "room_default": "room_member",
    "saved_group": "saved_group_member",
    "all_agents": "all_active_agents",
}


def _build_candidate_scope(
    *,
    room_id: str,
    agent_ids: list[str],
    scope_source: str = "explicit_selection",
    group_id: str | None = None,
    requesting_subject_id: str | None = None,
) -> CandidateScopeSnapshot:
    basis = AuthorizationBasis(
        kind=_SCOPE_AUTHORIZATION_KINDS.get(scope_source, "explicit_selection"),
        room_id=room_id,
        group_id=group_id,
        selected_by_user_id=requesting_subject_id or None,
    )
    return CandidateScopeSnapshot(
        snapshot_id=f"scope-{_sha256_hex(json.dumps([room_id, sorted(agent_ids), scope_source, group_id or '']))}",
        source=scope_source,
        room_id=room_id,
        group_id=group_id,
        agent_ids=list(dict.fromkeys(agent_ids)),
        authorization_basis=basis,
    )


def _build_resource_manifest(
    *,
    source_message_id: str,
    user_text: str | None,
    attachments: list[AttachmentEnvelope] | None,
) -> RunResourceManifestSnapshot:
    from context_memory.resources import (
        AttachmentResource,
        ResourceCatalogSource,
        assemble_resource_catalog,
    )

    entries = assemble_resource_catalog(
        ResourceCatalogSource(
            user_message_id=source_message_id,
            user_text=user_text,
            attachments=[
                AttachmentResource(
                    file_id=attachment.file_id,
                    mime_type=attachment.mime_type,
                    size_bytes=attachment.size_bytes,
                    content_digest=attachment.content_digest,
                )
                for attachment in (attachments or [])
            ],
        )
    )
    refs = [
        PreparedResourceRef(
            ref_id=entry.ref_id,
            kind=entry.kind,
            source_message_id=entry.source_message_id,
            mime_type=entry.mime_type,
            size_bytes=entry.size_bytes,
            content_digest=entry.content_digest,
        )
        for entry in entries
    ]
    return RunResourceManifestSnapshot(
        manifest_id=f"manifest-{_sha256_hex(json.dumps([ref.model_dump(mode='json') for ref in refs]))}",
        refs=refs,
        content_digest=_sha256_hex(
            json.dumps([ref.model_dump(mode="json") for ref in refs])
        ),
    )


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _map_legacy_answers(
    spec: A2AInteractionSpec, answers: list[dict[str, str]]
) -> list[HITLQuestionAnswer]:
    questions = {question.question_id: question for question in spec.questions}
    mapped: list[HITLQuestionAnswer] = []
    for raw in answers:
        question_id = raw.get("request_id")
        user_input = raw.get("user_input")
        if question_id not in questions:
            raise UnsupportedEnvelopeError(
                f"legacy HITL answer {question_id!r} has no orchestrator question"
            )
        question = questions[question_id]
        mapped.append(
            HITLQuestionAnswer(
                question_id=question_id,
                answer=_answer_for_kind(question.answer_kind, user_input or ""),
            )
        )
    if set(questions) != {answer.question_id for answer in mapped}:
        raise UnsupportedEnvelopeError(
            "legacy HITL answers do not match the orchestrator interaction inventory"
        )
    return mapped


def _answer_for_kind(kind: HITLAnswerKind, user_input: str) -> Any:
    if kind == HITLAnswerKind.TEXT:
        return HITLTextAnswer(text=user_input)
    if kind == HITLAnswerKind.SINGLE_CHOICE:
        return HITLSingleChoiceAnswer(choice=user_input)
    if kind == HITLAnswerKind.MULTI_CHOICE:
        choices = [choice.strip() for choice in user_input.split(",") if choice.strip()]
        return HITLMultiChoiceAnswer(choices=choices)
    if kind == HITLAnswerKind.CONFIRMATION:
        normalized = user_input.strip().lower()
        return HITLConfirmationAnswer(
            confirmed=normalized in {"true", "yes", "1", "confirmed", "approve"}
        )
    raise UnsupportedEnvelopeError(
        f"orchestrator cannot serve HITL answer kind {kind.value!r} from legacy text"
    )


def _observation_from_webhook_payload(
    payload: dict[str, Any], call: Any
) -> NormalizedA2AObservation:
    """Minimal webhook normalization for the step-7 dark-launch path.

    Handles the two most common A2A StreamResponse envelopes (``task`` and
    ``statusUpdate``). The observation identity is derived from the resolved
    ledger call, so lineage stays durable even when the payload omits task ids.
    """
    source = (
        payload.get("result") if isinstance(payload.get("result"), dict) else payload
    )
    task_id, context_id, status, text = _extract_webhook_identity(source)
    event_kind = _event_kind_for_status(status)
    content = [TextPart(text=text)] if text else []
    return NormalizedA2AObservation(
        observation_id=(
            f"webhook-{_sha256_hex(json.dumps([call.call_record_id, task_id, status, text]))}"
        ),
        call_record_id=call.call_record_id,
        source_kind="webhook",
        source_identity=f"webhook:{call.call_record_id}:{task_id or context_id or ''}",
        binding_scope=call.endpoint_scope_digest,
        event_kind=event_kind,
        observed_at=datetime.now(UTC),
        task_id=task_id or call.a2a_task_id,
        context_id=context_id or call.a2a_context_id,
        agent_id=call.agent_id,
        status=status if event_kind == "terminal" else None,
        content=content,
        artifact_refs=[],
        interaction_spec=None,
        error_code=None,
        error_message=None,
    )


def _extract_webhook_identity(
    source: dict[str, Any],
) -> tuple[str | None, str | None, str | None, str]:
    if isinstance(source.get("task"), dict):
        task = source["task"]
        status = _status_value(task.get("status"))
        text = _extract_webhook_text(task)
        return (
            _first_str(task.get("id"), task.get("task_id"), task.get("taskId")),
            _first_str(
                task.get("context_id"), task.get("contextId"), task.get("contextId")
            ),
            status,
            text,
        )
    raw = source.get("statusUpdate") or source.get("status_update")
    if isinstance(raw, dict):
        status = _status_value(raw.get("status"))
        text = _extract_webhook_text(raw.get("status"))
        return (
            _first_str(raw.get("task_id"), raw.get("taskId")),
            _first_str(raw.get("context_id"), raw.get("contextId")),
            status,
            text,
        )
    message = source.get("message")
    if isinstance(message, dict):
        return (
            _first_str(message.get("task_id"), message.get("taskId")),
            _first_str(message.get("context_id"), message.get("contextId")),
            "completed",
            _extract_webhook_text(message),
        )
    return None, None, None, ""


def _status_value(status: Any) -> str | None:
    if not isinstance(status, dict):
        return None
    value = status.get("state")
    if not isinstance(value, str):
        return None
    return value


def _first_str(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value
    return None


def _extract_webhook_text(value: Any) -> str:  # noqa: C901
    if not isinstance(value, dict):
        return ""
    message = value.get("message")
    if isinstance(message, dict):
        parts = message.get("parts") or message.get("content")
        if isinstance(parts, list):
            texts = []
            for part in parts:
                if isinstance(part, dict):
                    text = part.get("text")
                    if isinstance(text, str) and text.strip():
                        texts.append(text)
            if texts:
                return "".join(texts)
        text = message.get("text")
        if isinstance(text, str):
            return text
    artifacts = value.get("artifacts")
    if isinstance(artifacts, list):
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            text = _extract_webhook_text(artifact)
            if text:
                return text
    return ""


def _event_kind_for_status(status: str | None) -> str:
    if status in {"input-required", "input_required"}:
        return "input_required"
    if status in {"auth-required", "auth_required"}:
        return "auth_required"
    if status in {"completed", "failed", "canceled", "rejected", "expired"}:
        return "terminal"
    return "working"


class _PreparedRunFactory:
    """Run factory that pins the Run id the catalog was prepared against."""

    def __init__(self, run_id: str, base: RunFactory) -> None:
        self._run_id = run_id
        self._base = base

    def create_run(
        self,
        *,
        config: Any,
        message: UserMessage,
        client_request_id: str | None,
    ) -> Any:
        run = self._base.create_run(
            config=config, message=message, client_request_id=client_request_id
        )
        return run.model_copy(update={"run_id": self._run_id})


class DualRuntimeRouter:
    """Ownership-aware dispatcher shared by every legacy ingress.

    Construction is cheap and non-IO. When ``runtime`` is ``None`` (or the
    orchestrator composition was disabled) every decision falls back to
    ``legacy`` and every ingress route stays on the legacy path.
    """

    def __init__(
        self,
        *,
        runtime: Any | None = None,
        settings: Any | None = None,
        envelope_source: RoomEnvelopeSource | None = None,
        run_factory: RunFactory | None = None,
        webhook_token_verifier: WebhookTokenVerifier | None = None,
    ) -> None:
        self._runtime = runtime
        self._settings = settings
        self._envelope_source = envelope_source
        self._run_factory = run_factory or DefaultRunFactory()
        self._webhook_token_verifier = webhook_token_verifier

    # -- Run-creation decision ------------------------------------------

    async def assign_runtime(
        self,
        *,
        room_id: str,
        client_request_id: str | None,
        user_id: str | None,
        mode: str,
        agent_scope: dict[str, Any] | None = None,
    ) -> str:
        """Decide the runtime for a *new* Run. Never re-evaluated afterwards."""
        if self._runtime is None or self._settings is None:
            return OWNER_LEGACY
        if getattr(self._settings, "orchestrator_kill_switch", False):
            return OWNER_LEGACY
        if not getattr(self._settings, "orchestrator_routing_enabled", False):
            return OWNER_LEGACY
        if agent_scope is not None:
            # The agent scope is part of the orchestration request; the seam
            # must be able to serve it before ownership is decided. Scopes
            # outside the closed API enumeration stay on the legacy executor.
            source = (
                agent_scope.get("source") if isinstance(agent_scope, dict) else None
            )
            if source not in _SERVABLE_SCOPE_SOURCES:
                return OWNER_LEGACY
        profile_id = map_mode_to_profile(mode)
        profiles = getattr(self._runtime, "profiles", None)
        if profiles is not None and profile_id not in profiles:
            return OWNER_LEGACY

        user_allowlist = _allowlist(
            getattr(self._settings, "orchestrator_user_allowlist", [])
        )
        room_allowlist = _allowlist(
            getattr(self._settings, "orchestrator_room_allowlist", [])
        )
        if user_id in user_allowlist or room_id in room_allowlist:
            return OWNER_ORCHESTRATOR
        if user_allowlist or room_allowlist:
            return OWNER_LEGACY

        ratio = int(getattr(self._settings, f"orchestrator_{profile_id}_ratio", 0) or 0)
        if ratio <= 0:
            return OWNER_LEGACY
        bucket = stable_route_bucket(room_id, client_request_id)
        return OWNER_ORCHESTRATOR if bucket < ratio else OWNER_LEGACY

    # -- Persisted-ownership resolution ---------------------------------

    async def resolve_run_owner(self, run_id: str) -> str:
        if self._runtime is None:
            return OWNER_LEGACY
        run = await self._runtime.run_store.load(run_id)
        return OWNER_ORCHESTRATOR if run is not None else OWNER_LEGACY

    async def resolve_run_owner_by_user_message(self, user_message_id: str) -> str:
        """Correlate ownership by the originating room user message id.

        The public cancel path is keyed by the room user message id (not the
        orchestrator ``run_id``), so it must resolve through
        ``RunRequestSnapshot.user_message_id``.
        """
        if self._runtime is None:
            return OWNER_LEGACY
        run = await self._runtime.run_store.load_by_user_message_id(user_message_id)
        return OWNER_ORCHESTRATOR if run is not None else OWNER_LEGACY

    async def resolve_call_owner(
        self,
        *,
        binding_scope: str | None,
        task_id: str | None,
        context_id: str | None,
        call_record_id: str | None,
    ) -> str:
        if self._runtime is None:
            return OWNER_LEGACY
        ledger = self._runtime.call_ledger
        if call_record_id:
            call = await ledger.load_by_record_id(call_record_id)
        elif binding_scope:
            call = await ledger.find_by_alias(
                binding_scope, task_id=task_id, context_id=context_id
            )
        elif task_id:
            call = await ledger.find_by_task_id(task_id)
        else:
            return OWNER_LEGACY
        return OWNER_ORCHESTRATOR if call is not None else OWNER_LEGACY

    async def _resolve_webhook_call(self, message_id: str) -> Any | None:
        """Resolve a webhook by A2A task id first, then by call record id."""
        ledger = self._runtime.call_ledger
        call = await ledger.find_by_task_id(message_id)
        if call is None:
            call = await ledger.load_by_record_id(message_id)
        return call

    async def resolve_interaction_owner(self, interaction_id: str) -> str:
        if self._runtime is None:
            return OWNER_LEGACY
        stored = await self._runtime.hitl_store.load_interaction(interaction_id)
        return OWNER_ORCHESTRATOR if stored is not None else OWNER_LEGACY

    # -- Ingress routing -------------------------------------------------

    async def record_observation(
        self, observation: NormalizedA2AObservation
    ) -> tuple[str, Any]:
        if self._runtime is None:
            raise OrchestratorRoutingError("orchestrator ingress is not bound")
        return await self._runtime.observation_ingress.record(observation)

    async def route_cancellation(
        self,
        run_id: str,
        *,
        reason: str,
        deletion_id: str | None = None,
    ) -> dict[str, str]:
        if self._runtime is None:
            raise OrchestratorRoutingError("orchestrator cancellation is not bound")
        return await self._runtime.cancellation_coordinator.cancel_run(
            run_id, reason=reason, deletion_id=deletion_id
        )

    async def route_cancellation_by_user_message(
        self,
        user_message_id: str,
        *,
        reason: str,
        deletion_id: str | None = None,
    ) -> dict[str, str]:
        """Cancel an orchestrator Run correlated by its room user message id."""
        if self._runtime is None:
            raise OrchestratorRoutingError("orchestrator cancellation is not bound")
        run = await self._runtime.run_store.load_by_user_message_id(user_message_id)
        if run is None:
            raise KeyError(user_message_id)
        return await self._runtime.cancellation_coordinator.cancel_run(
            run.run_id, reason=reason, deletion_id=deletion_id
        )

    async def route_hitl_answer(
        self,
        *,
        interaction_id: str,
        answers: list[dict[str, str]],
        responder_id: str,
        room_id: str,
    ) -> str:
        if self._runtime is None:
            raise OrchestratorRoutingError("orchestrator HITL ingress is not bound")
        read = await self._runtime.hitl_port.read_interaction(interaction_id)
        if read is None:
            raise KeyError(interaction_id)
        spec, route, _fingerprint = read
        if route.room_id != room_id:
            raise HITLRoomMismatchError("Room mismatch")
        mapped = _map_legacy_answers(spec, answers)
        return await self._runtime.hitl_port.answer(
            interaction_id=interaction_id,
            interaction_revision=route.interaction_revision,
            route_fingerprint=route.fingerprint,
            answers=mapped,
            authenticated_answerer_id=responder_id,
            verified_auth_reference_digests=[],
            verified_auth_references=[],
        )

    async def _authenticate_webhook(self, message_id: str, token: str) -> None:
        """Authenticate an orchestrator webhook exactly like the legacy route."""
        if self._webhook_token_verifier is None:
            raise OrchestratorRoutingError(
                "orchestrator webhook token verifier is not bound"
            )
        if not token:
            raise WebhookAuthenticationError(401, "Missing authorization token")
        is_valid, error_reason = await self._webhook_token_verifier(message_id, token)
        if not is_valid:
            if error_reason == "task_not_found":
                raise WebhookAuthenticationError(
                    404, "Task not found. The task may not have been created yet."
                )
            if error_reason == "invalid_token":
                raise WebhookAuthenticationError(401, "Invalid token")
            raise WebhookAuthenticationError(500, "Token verification failed")

    async def route_webhook(
        self, *, message_id: str, payload: dict[str, Any], token: str
    ) -> str:
        """Record an authenticated orchestrator-owned webhook, or fall through.

        Correlation resolves the A2A ``task_id`` alias first and then falls back
        to the orchestrator ``call_record_id``; any webhook that does not match
        an orchestrator call stays on the legacy path. Orchestrator-owned
        webhooks authenticate against the call's room-scoped assistant message
        id through the injected legacy token verifier
        (``verify_webhook_token_for_task``); the legacy token store is keyed by
        room message ids, so authentication must go through this seam rather
        than the legacy ``transport.authenticate_webhook`` path (which is keyed
        by the URL path id). A failing webhook raises
        ``WebhookAuthenticationError`` carrying the same HTTP status as the
        legacy authenticator.
        """
        if self._runtime is None:
            return OWNER_LEGACY
        owner = await self.resolve_call_owner(
            binding_scope=None,
            task_id=message_id,
            context_id=None,
            call_record_id=None,
        )
        if owner != OWNER_ORCHESTRATOR:
            owner = await self.resolve_call_owner(
                binding_scope=None,
                task_id=None,
                context_id=None,
                call_record_id=message_id,
            )
        if owner != OWNER_ORCHESTRATOR:
            return OWNER_LEGACY
        call = await self._resolve_webhook_call(message_id)
        if call is None:
            return OWNER_LEGACY
        await self._authenticate_webhook(call.assistant_message_id, token)
        observation = _observation_from_webhook_payload(payload, call)
        await self._runtime.observation_ingress.record(observation)
        return OWNER_ORCHESTRATOR

    # -- RoomMessageCenterPort adapter ------------------------------------

    async def _resolve_envelope_and_profile(
        self, request: OrchestrationRequest
    ) -> tuple[RoomMessageEnvelope, Any]:
        """Resolve and validate the envelope without any orchestrator side effect."""
        if self._runtime is None:
            raise OrchestratorRoutingError("orchestrator message adapter is not bound")
        if self._envelope_source is None:
            raise UnsupportedEnvelopeError(
                "orchestrator message adapter is not bound to a room envelope source"
            )
        room_id = request.room_id
        if not room_id:
            raise UnsupportedEnvelopeError("orchestrator requires room_id")

        envelope = await self._envelope_source.load_envelope(request)
        profile_id = map_mode_to_profile(envelope.mode)
        profile = self._runtime.profiles.get(profile_id)
        if profile is None:
            raise UnsupportedEnvelopeError(
                f"orchestrator profile {profile_id!r} is not resolved"
            )
        if (
            profile.initial_routing != _PROFILE_PINNED_INITIAL_ROUTING
            or profile.finalization != _PROFILE_PINNED_FINALIZATION
        ):
            raise UnsupportedEnvelopeError(
                "orchestrator cannot yet serve this profile's reserved "
                "routing/finalization dimensions"
            )
        return envelope, profile

    async def preflight_room_user_message(self, request: OrchestrationRequest) -> None:
        """Validate servability before Run assignment side effects.

        Resolves the persisted envelope, profile, and candidate scope without
        creating a session, epoch, or Run. ``UnsupportedEnvelopeError`` means
        the legacy engine must serve the message.
        """
        await self._resolve_envelope_and_profile(request)

    async def process_room_user_message(
        self, request: OrchestrationRequest
    ) -> OrchestrationResponse:
        envelope, profile = await self._resolve_envelope_and_profile(request)
        room_id = request.room_id

        requesting_subject_id = envelope.requesting_subject_id or request.user_id or ""
        candidate_scope = _build_candidate_scope(
            room_id=room_id,
            agent_ids=envelope.candidate_agent_ids,
            scope_source=envelope.scope_source,
            group_id=envelope.group_id,
            requesting_subject_id=requesting_subject_id,
        )
        if not candidate_scope.agent_ids:
            # An empty scope cannot produce a meaningful kernel run; keep the
            # legacy executor's empty-scope behavior until the seam grows a
            # zero-candidate synthesis path.
            raise UnsupportedEnvelopeError(
                "orchestrator candidate scope resolved to zero agents"
            )
        resource_manifest = _build_resource_manifest(
            source_message_id=request.room_user_message_id or "",
            user_text=envelope.message_text,
            attachments=envelope.attachments,
        )

        session_host = self._runtime.session_host
        session = session_host.get_session(room_id)
        if session is not None:
            if await session.has_active_run():
                raise SessionConflict("a Run is already active for this Room")
            # A session pins ONE Run id and ONE frozen catalog, so an idle
            # (terminal) session is replaced by a freshly prepared one for
            # every new message instead of replaying the stale Run id.
            session_host.drop_session(room_id)
        epoch = await self._runtime.epoch_store.read_active(room_id)
        if epoch is None:
            raise UnsupportedEnvelopeError("Room epoch is not active")
        run_id = f"run-{uuid4().hex}"
        prepared = await self._runtime.catalog_assembler.prepare(
            run_id=run_id,
            room_id=room_id,
            room_epoch=epoch.epoch,
            requesting_subject_id=requesting_subject_id,
            candidate_scope=candidate_scope,
            resource_manifest=resource_manifest,
            authorization_basis_digest=_sha256_hex(
                json.dumps(
                    candidate_scope.authorization_basis.model_dump(mode="json"),
                    sort_keys=True,
                )
            ),
            created_at=datetime.now(UTC),
        )
        await session_host.create_session(
            room_id=room_id,
            profile=profile,
            candidate_scope=candidate_scope,
            requesting_subject_id=requesting_subject_id,
            frozen_catalog=prepared.snapshot,
            resource_manifest=resource_manifest,
            run_factory=_PreparedRunFactory(run_id, self._run_factory),
        )

        message = UserMessage(
            message_id=request.room_user_message_id or f"user-{uuid4().hex}",
            content=[
                TextPart(text=envelope.message_text),
                *[TextPart(text=block) for block in envelope.attachment_texts],
            ],
            created_at=datetime.now(UTC),
        )
        result = await session_host.prompt(
            room_id,
            message,
            client_request_id=request.client_request_id,
        )
        return OrchestrationResponse(
            task_id=result.run.run_id if getattr(result, "run", None) else None,
            room_id=room_id,
            success=True,
            status_code=200,
        )


__all__ = [
    "AttachmentEnvelope",
    "DualRuntimeRouter",
    "MODE_PROFILE_MAP",
    "OWNER_LEGACY",
    "_SERVABLE_SCOPE_SOURCES",
    "OWNER_ORCHESTRATOR",
    "OrchestratorRoutingError",
    "RoomEnvelopeSource",
    "RoomMessageEnvelope",
    "RoomMessageEnvelopeResolver",
    "UnsupportedEnvelopeError",
    "WebhookAuthenticationError",
    "WebhookTokenVerifier",
    "map_mode_to_profile",
    "stable_route_bucket",
]
