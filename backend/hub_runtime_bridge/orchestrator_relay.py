"""Outgoing relay command journal and sender for the orchestrator boundary.

``hub_runtime_bridge`` owns the durable outbound relay command journal. The
existing ``hub_response_journal`` only covers *inbound* hub→backend responses;
this module is a separate, purpose-built journal for *outgoing* orchestrator
commands so replay/dedupe semantics never share an identity space with inbound
responses.

Like the direct client adapter, the orchestrator command DTOs cross this
boundary by structural typing (see the local ``Protocol`` mirrors below), and
the provider-neutral ``A2ADispatchReceipt`` is reconstructed through an injected
factory. ``hub_runtime_bridge`` does not import ``execution.orchestrator``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

from pymongo.errors import DuplicateKeyError

from common.dto import HubCancelCommand, HubDispatchCommand, HubReplyCommand
from common.orchestrator_constants import RELAY_COMMAND_JOURNAL_COLLECTION
from common.utils.time import utcnow

# ---------------------------------------------------------------------------
# Provider-neutral command mirrors.
# ---------------------------------------------------------------------------


class _DispatchCommand(Protocol):
    command_id: str
    call_record_id: str
    message_id: str
    agent_id: str
    skill_id: str | None
    endpoint_scope: str
    transport_kind: str
    task: str
    materialized_resources: list[Any]
    room_id: str
    room_epoch: int
    deadline_at: Any


class _ContinuationCommand(Protocol):
    command_id: str
    call_record_id: str
    interaction_id: str
    answer_digest: str
    answers: list[Any]
    task_id: str
    context_id: str
    room_id: str
    room_epoch: int
    created_at: Any


class _CancellationCommand(Protocol):
    command_id: str
    call_record_id: str
    reason: str
    deletion_id: str | None
    created_at: Any


# ---------------------------------------------------------------------------
# Journal store (in-memory for tests, Mongo for production wiring in step 5b).
# ---------------------------------------------------------------------------


class RelayCommandJournalStore(Protocol):
    async def persist(
        self, *, command_id: str, kind: str, command: dict[str, Any]
    ) -> str: ...

    async def record_receipt(
        self, command_id: str, receipt: dict[str, Any]
    ) -> None: ...

    async def load(self, command_id: str) -> dict[str, Any] | None: ...

    async def load_receipt(self, command_id: str) -> dict[str, Any] | None: ...


class InMemoryRelayCommandJournalStore:
    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}

    async def persist(
        self, *, command_id: str, kind: str, command: dict[str, Any]
    ) -> str:
        existing = self._records.get(command_id)
        if existing is not None:
            return "replayed" if existing["kind"] == kind else "conflict"
        self._records[command_id] = {
            "command_id": command_id,
            "kind": kind,
            "command": dict(command),
            "receipt": None,
            "created_at": utcnow(),
        }
        return "accepted"

    async def record_receipt(self, command_id: str, receipt: dict[str, Any]) -> None:
        record = self._records.get(command_id)
        if record is not None:
            record["receipt"] = dict(receipt)

    async def load(self, command_id: str) -> dict[str, Any] | None:
        record = self._records.get(command_id)
        return dict(record) if record is not None else None

    async def load_receipt(self, command_id: str) -> dict[str, Any] | None:
        record = self._records.get(command_id)
        if record is None or record.get("receipt") is None:
            return None
        return dict(record["receipt"])


class MongoRelayCommandJournalStore:
    def __init__(self, mongo: Any) -> None:
        collection_factory = getattr(mongo, "collection", None)
        if callable(collection_factory):
            self._collection = collection_factory(RELAY_COMMAND_JOURNAL_COLLECTION)
        else:
            self._collection = mongo.db[RELAY_COMMAND_JOURNAL_COLLECTION]

    async def persist(
        self, *, command_id: str, kind: str, command: dict[str, Any]
    ) -> str:
        existing = await self._collection.find_one({"command_id": command_id})
        if existing is not None:
            return "replayed" if existing.get("kind") == kind else "conflict"
        try:
            await self._collection.insert_one(
                {
                    "command_id": command_id,
                    "kind": kind,
                    "command": dict(command),
                    "receipt": None,
                    "created_at": utcnow(),
                }
            )
        except DuplicateKeyError:
            winner = await self._collection.find_one({"command_id": command_id})
            if winner is None:
                raise
            return "replayed" if winner.get("kind") == kind else "conflict"
        return "accepted"

    async def record_receipt(self, command_id: str, receipt: dict[str, Any]) -> None:
        await self._collection.update_one(
            {"command_id": command_id}, {"$set": {"receipt": dict(receipt)}}
        )

    async def load(self, command_id: str) -> dict[str, Any] | None:
        doc = await self._collection.find_one({"command_id": command_id})
        return dict(doc) if doc is not None else None

    async def load_receipt(self, command_id: str) -> dict[str, Any] | None:
        doc = await self._collection.find_one({"command_id": command_id})
        if doc is None or doc.get("receipt") is None:
            return None
        return dict(doc["receipt"])


# ---------------------------------------------------------------------------
# Journal + sender adapters.
# ---------------------------------------------------------------------------


class RelayCommandJournal:
    def __init__(
        self,
        *,
        store: RelayCommandJournalStore,
        receipt_factory: Callable[..., Any],
    ) -> None:
        self._store = store
        self._receipt = receipt_factory

    async def persist_dispatch(self, command: _DispatchCommand) -> str:
        return await self._store.persist(
            command_id=command.command_id,
            kind="dispatch",
            command=_serialize(command),
        )

    async def persist_continuation(self, command: _ContinuationCommand) -> str:
        return await self._store.persist(
            command_id=command.command_id,
            kind="continuation",
            command=_serialize(command),
        )

    async def persist_cancellation(self, command: _CancellationCommand) -> str:
        return await self._store.persist(
            command_id=command.command_id,
            kind="cancellation",
            command=_serialize(command),
        )

    async def inspect(self, command_id: str) -> Any:
        receipt = await self._store.load_receipt(command_id)
        if receipt is None:
            return self._receipt(outcome="delivery_uncertain")
        return self._receipt(**receipt)


class RelayCommandSender:
    def __init__(
        self,
        *,
        relay_service: Any,
        store: RelayCommandJournalStore,
        receipt_factory: Callable[..., Any],
        local_agent_id_resolver: Callable[[str], Awaitable[str | None]] | None = None,
        call_resolver: Callable[[str], Awaitable[Mapping[str, Any] | None]]
        | None = None,
    ) -> None:
        self._relay_service = relay_service
        self._store = store
        self._receipt = receipt_factory
        self._local_agent_id_resolver = local_agent_id_resolver
        self._call_resolver = call_resolver
        self._addresses: dict[str, dict[str, Any]] = {}

    def _remember(
        self,
        command: Any,
        *,
        endpoint_scope: str,
        agent_id: str,
        message_id: str,
        local_agent_id: str,
        task_id: str | None = None,
        context_id: str | None = None,
    ) -> None:
        self._addresses[command.call_record_id] = {
            "endpoint_scope": endpoint_scope,
            "agent_id": agent_id,
            "message_id": message_id,
            "local_agent_id": local_agent_id,
            "task_id": task_id,
            "context_id": context_id,
        }

    async def _resolve_address(self, call_record_id: str) -> dict[str, Any]:
        address = self._addresses.get(call_record_id)
        if address is None and self._call_resolver is not None:
            raw = await self._call_resolver(call_record_id)
            if raw is not None:
                address = dict(raw)
                self._addresses[call_record_id] = address
        return address or {"call_record_id": call_record_id}

    async def _resolve_local_agent_id(self, agent_id: str) -> str:
        if self._local_agent_id_resolver is None:
            return agent_id
        return (await self._local_agent_id_resolver(agent_id)) or agent_id

    async def _record(self, command_id: str, receipt: Any) -> None:
        # Fire-and-forget durability; a missing receipt simply becomes
        # delivery_uncertain on replay and is reconciled by inspection.
        if hasattr(receipt, "model_dump"):
            await self._store.record_receipt(
                command_id, receipt.model_dump(mode="json")
            )

    async def send_dispatch(self, command: _DispatchCommand) -> Any:
        local_agent_id = await self._resolve_local_agent_id(command.agent_id)
        hub_command = HubDispatchCommand(
            hub_id=command.endpoint_scope,
            agent_id=command.agent_id,
            local_agent_id=local_agent_id,
            room_id=command.room_id,
            user_message_id=command.message_id,
            agent_message_id=command.message_id,
            payload=_relay_dispatch_payload(command),
            task_id=None,
            task_data={},
        )
        self._remember(
            command,
            endpoint_scope=command.endpoint_scope,
            agent_id=command.agent_id,
            message_id=command.message_id,
            local_agent_id=local_agent_id,
        )
        result = await self._relay_service.send_to_hub(hub_command)
        receipt = self._receipt(
            outcome="accepted" if result.accepted else "delivery_uncertain",
            task_id=result.task_id,
            context_id=None,
        )
        await self._record(command.command_id, receipt)
        return receipt

    async def send_continuation(self, command: _ContinuationCommand) -> Any:
        address = await self._resolve_address(command.call_record_id)
        hub_id = address.get("endpoint_scope")
        agent_message_id = address.get("message_id")
        local_agent_id = address.get("local_agent_id")
        if not hub_id or not agent_message_id:
            return self._receipt(outcome="delivery_uncertain")
        hub_command = HubReplyCommand(
            hub_id=hub_id,
            agent_message_id=agent_message_id,
            local_agent_id=local_agent_id or agent_message_id,
            room_id=command.room_id,
            reply_text=_continuation_text(command.answers),
            task_id=command.task_id,
            context_id=command.context_id,
        )
        acknowledged = await self._relay_service.reply_to_hub_task(hub_command)
        receipt = self._receipt(
            outcome="accepted" if acknowledged else "delivery_uncertain",
            task_id=command.task_id,
            context_id=command.context_id,
        )
        await self._record(command.command_id, receipt)
        return receipt

    async def send_cancellation(self, command: _CancellationCommand) -> Any:
        address = await self._resolve_address(command.call_record_id)
        hub_id = address.get("endpoint_scope")
        agent_message_id = address.get("message_id")
        local_agent_id = address.get("local_agent_id")
        if not hub_id or not agent_message_id:
            return self._receipt(outcome="delivery_uncertain")
        hub_command = HubCancelCommand(
            hub_id=hub_id,
            agent_message_id=agent_message_id,
            local_agent_id=local_agent_id or agent_message_id,
            task_id=address.get("task_id"),
        )
        acknowledged = await self._relay_service.cancel_hub_task(hub_command)
        receipt = self._receipt(
            outcome="accepted" if acknowledged else "delivery_uncertain",
            task_id=address.get("task_id"),
            context_id=address.get("context_id"),
        )
        await self._record(command.command_id, receipt)
        return receipt


def _serialize(command: Any) -> dict[str, Any]:
    if hasattr(command, "model_dump"):
        return command.model_dump(mode="json")
    if hasattr(command, "__dict__"):
        return dict(command.__dict__)
    return dict(command)


def _relay_dispatch_payload(command: _DispatchCommand) -> dict[str, Any]:
    return {
        "task": command.task,
        "skill_id": command.skill_id,
        "resources": [
            _serialize(resource) for resource in command.materialized_resources
        ],
    }


def _continuation_text(answers: list[Any]) -> str:
    parts: list[str] = []
    for answer in answers:
        question_id = getattr(answer, "question_id", None)
        value = getattr(answer, "answer", None)
        text = _answer_text(value)
        if text:
            parts.append(f"{question_id}: {text}" if question_id else text)
    return "\n".join(parts) if parts else "continue"


def _answer_text(value: Any) -> str:
    if value is None:
        return ""
    text = getattr(value, "text", None)
    if isinstance(text, str):
        return text
    choice = getattr(value, "choice", None)
    if isinstance(choice, str):
        return choice
    choices = getattr(value, "choices", None)
    if isinstance(choices, list):
        return ", ".join(str(item) for item in choices)
    confirmed = getattr(value, "confirmed", None)
    if confirmed is not None:
        return str(bool(confirmed))
    decision = getattr(value, "decision", None)
    if decision is not None:
        return str(getattr(decision, "value", decision))
    reference = getattr(value, "authorization_reference", None)
    if isinstance(reference, str):
        return reference
    if hasattr(value, "model_dump"):
        return str(value.model_dump(mode="json"))
    return str(value)


__all__ = [
    "InMemoryRelayCommandJournalStore",
    "MongoRelayCommandJournalStore",
    "RELAY_COMMAND_JOURNAL_COLLECTION",
    "RelayCommandJournal",
    "RelayCommandSender",
    "RelayCommandJournalStore",
]
