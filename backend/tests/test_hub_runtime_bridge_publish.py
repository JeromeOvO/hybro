from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.dto import HubPublishLineageSnapshot
from common.utils.time import utcnow
from execution.dispatch.response_handler import AgentResponseHandler
from execution.facade import hub_agent_response_internal_to_agent_event
from hub_runtime_bridge.hub_response_journal import InMemoryHubResponseJournal
from hub_runtime_bridge.internal_response_router import HubInternalResponseRouter
from hub_runtime_bridge.service.hub_publish import (
    HubPublishService,
    normalize_hub_publish_payload,
)
from hub_runtime_bridge.service.hub_response_replay_worker import (
    HubResponseReplayWorker,
)
from hub_runtime_bridge.task_ownership import InMemoryHubTaskOwnershipStore


class Dispatcher:
    def __init__(self) -> None:
        self.events = []

    async def dispatch_hub_internal_response(self, event):
        self.events.append(event)


class Sink:
    def __init__(self) -> None:
        self.events = []

    async def handle_hub_agent_response(self, event):
        self.events.append(event)


class AuthorizationReader:
    def __init__(self, lineage=None) -> None:
        self.lineage = lineage
        self.calls = []

    async def authorize_hub_publish(self, **kwargs):
        self.calls.append(kwargs)
        return self.lineage


class CancellationReader:
    def __init__(self, cancelled: set[str] | None = None) -> None:
        self.cancelled = cancelled or set()

    async def is_message_cancelled(self, message_id: str) -> bool:
        return message_id in self.cancelled


class EventPublisher:
    def __init__(self) -> None:
        self.events = []

    async def emit_internal(self, event):
        self.events.append(event)


class NoopDispatcher:
    async def dispatch_hub_internal_response(self, event):
        return None


def test_normalized_publish_payload_backfills_legacy_text_keys() -> None:
    response = normalize_hub_publish_payload(
        "agent_response",
        "msg-1",
        {"task_id": "task-1", "text": "hello"},
        task_id="task-1",
    )
    error = normalize_hub_publish_payload(
        "agent_error",
        "msg-1",
        {"task_id": "task-1", "error_text": "bad"},
        task_id="task-1",
    )

    assert response["content"] == "hello"
    assert error["error"] == "bad"


def test_normalized_agent_response_converts_file_parts() -> None:
    payload = normalize_hub_publish_payload(
        "agent_response",
        "msg-1",
        {
            "task_id": "task-1",
            "content": "file",
            "parts": [
                {
                    "raw": "abc",
                    "mediaType": "text/plain",
                    "filename": "a.txt",
                }
            ],
        },
        task_id="task-1",
    )

    assert payload["parts"] == [
        {
            "kind": "file",
            "file": {
                "bytes": "abc",
                "mimeType": "text/plain",
                "name": "a.txt",
            },
        }
    ]


def test_normalized_agent_response_deduplicates_file_parts() -> None:
    payload = normalize_hub_publish_payload(
        "agent_response",
        "msg-1",
        {
            "task_id": "task-1",
            "content": "file",
            "parts": [
                {
                    "raw": "abc",
                    "mediaType": "text/plain",
                    "filename": "a.txt",
                },
                {
                    "raw": "abc",
                    "mediaType": "text/plain",
                    "filename": "a.txt",
                },
            ],
        },
        task_id="task-1",
    )

    assert len(payload["parts"]) == 1


@pytest.mark.asyncio
async def test_publish_response_alias_normalizes_legacy_parts_for_public_delivery() -> None:
    private_bytes = "PRIVATE_SENTINEL_response_inline_bytes"
    private_metadata = "PRIVATE_SENTINEL_response_metadata"
    converted_uri = "s3://public-artifacts/converted-inline.txt"
    existing_uri = "s3://public-artifacts/existing-report.pdf"
    legacy_file_uri = "s3://public-artifacts/legacy-file.json"
    dispatcher = Dispatcher()
    service = HubPublishService(dispatcher=dispatcher)

    await service.publish_from_hub(
        "hub-1",
        {
            "room_id": "room-1",
            "events": [
                {
                    "type": "response",
                    "agent_message_id": "msg-1",
                    "data": {
                        "task_id": "task-1",
                        "text": "Visible final answer",
                        "parts": [
                            {
                                "text": "Visible final answer",
                                "metadata": {"private": private_metadata},
                            },
                            {
                                "raw": private_bytes,
                                "mediaType": "text/plain",
                                "filename": "inline.txt",
                                "metadata": {"private": private_metadata},
                            },
                            {
                                "url": existing_uri,
                                "mediaType": "application/pdf",
                                "filename": "report.pdf",
                                "metadata": {"private": private_metadata},
                            },
                            {
                                "file": {
                                    "url": legacy_file_uri,
                                    "mediaType": "application/json",
                                    "filename": "legacy.json",
                                },
                                "metadata": {"private": private_metadata},
                            },
                        ],
                    },
                }
            ],
        },
    )

    assert len(dispatcher.events) == 1
    event = hub_agent_response_internal_to_agent_event(dispatcher.events[0])
    assert event.kind == "response"
    assert event.parts == [
        {
            "kind": "text",
            "text": "Visible final answer",
            "metadata": {"private": private_metadata},
        },
        {
            "kind": "file",
            "file": {
                "bytes": private_bytes,
                "mimeType": "text/plain",
                "name": "inline.txt",
            },
            "metadata": {"private": private_metadata},
        },
        {
            "kind": "file",
            "file": {
                "uri": existing_uri,
                "mimeType": "application/pdf",
                "name": "report.pdf",
            },
            "metadata": {"private": private_metadata},
        },
        {
            "kind": "file",
            "file": {
                "uri": legacy_file_uri,
                "mimeType": "application/json",
                "name": "legacy.json",
            },
            "metadata": {"private": private_metadata},
        },
    ]

    db = MagicMock()
    db.update_task_state_on_message = AsyncMock(return_value=(True, None))
    db.accumulate_artifact_on_message = AsyncMock(return_value=True)
    db.get_pending_continuation_on_message = AsyncMock(return_value=None)
    delivery = MagicMock()
    notification_impl = AsyncMock(return_value=True)
    handler = AgentResponseHandler(
        message_writer=db,
        task_writer=db,
        continuation_store=db,
        client_request_resolver=db,
        room_reader=db,
        hitl_reader=db,
        delivery=delivery,
        room_message_center=MagicMock(resume_queue_from_continuation=AsyncMock()),
        task_notifier=MagicMock(),
        task_notification_impl=notification_impl,
        task_notification_store=MagicMock(),
    )

    async def fake_convert_inline_bytes_to_s3(
        parts: list[dict],
        room_id: str,
        message_id: str,
        *,
        converted_so_far: int = 0,
    ) -> int:
        assert room_id == "room-1"
        assert message_id == "msg-1"
        for part in parts:
            assert "raw" not in part
            assert "url" not in part
            if part.get("kind") != "file":
                continue
            file_info = part.get("file")
            assert isinstance(file_info, dict)
            if file_info.get("bytes") == private_bytes:
                file_info.pop("bytes")
                file_info["uri"] = converted_uri
        return converted_so_far + 1

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "common.utils.a2a_helpers.convert_inline_bytes_to_s3",
            fake_convert_inline_bytes_to_s3,
        )
        await handler.handle(event)

    delivered_parts = notification_impl.await_args.kwargs["parts"]
    delivered_json = json.dumps(delivered_parts, sort_keys=True)
    assert converted_uri in delivered_json
    assert existing_uri in delivered_json
    assert legacy_file_uri in delivered_json
    assert private_bytes not in delivered_json
    assert private_metadata not in delivered_json
    assert '"raw"' not in delivered_json
    assert '"bytes"' not in delivered_json


def test_normalized_agent_response_converts_data_parts() -> None:
    payload = normalize_hub_publish_payload(
        "agent_response",
        "msg-1",
        {
            "task_id": "task-1",
            "content": "data",
            "parts": [{"data": {"value": 7}, "metadata": {"source": "hub"}}],
        },
        task_id="task-1",
    )

    assert payload["parts"] == [
        {
            "kind": "data",
            "data": {"value": 7},
            "metadata": {"source": "hub"},
        }
    ]


def test_normalized_agent_response_preserves_text_part_metadata() -> None:
    payload = normalize_hub_publish_payload(
        "agent_response",
        "msg-1",
        {
            "task_id": "task-1",
            "content": "text",
            "parts": [{"text": "hello", "metadata": {"source": "hub"}}],
        },
        task_id="task-1",
    )

    assert payload["parts"] == [
        {
            "kind": "text",
            "text": "hello",
            "metadata": {"source": "hub"},
        }
    ]


def test_normalized_interactive_event_preserves_prompt_text() -> None:
    payload = normalize_hub_publish_payload(
        "task_interactive",
        "msg-1",
        {
            "task_id": "task-1",
            "status_text": "Need approval",
        },
        task_id="task-1",
    )

    assert payload["state"] == "input-required"
    assert payload["text"] == "Need approval"


def test_normalized_processing_status_backfills_legacy_status_key() -> None:
    payload = normalize_hub_publish_payload(
        "processing_status",
        "msg-1",
        {"task_id": "task-1", "state": "failed"},
        task_id="task-1",
    )

    assert payload["state"] == "failed"
    assert payload["status"] == "failed"


def test_normalized_processing_status_defaults_to_completed() -> None:
    payload = normalize_hub_publish_payload(
        "processing_status",
        "msg-1",
        {"task_id": "task-1"},
        task_id="task-1",
    )

    assert payload["state"] == "completed"
    assert payload["status"] == "completed"


def test_normalized_processing_status_maps_legacy_input_required_to_awaiting_input() -> None:
    payload = normalize_hub_publish_payload(
        "processing_status",
        "msg-1",
        {"task_id": "task-1", "state": "input_required"},
        task_id="task-1",
    )

    assert payload["state"] == "awaiting_input"
    assert payload["status"] == "awaiting_input"


@pytest.mark.asyncio
async def test_publish_journals_before_internal_dispatch_and_preserves_legacy_repeats() -> None:
    journal = InMemoryHubResponseJournal()
    dispatcher = Dispatcher()
    service = HubPublishService(journal=journal, dispatcher=dispatcher)

    payload = {
        "room_id": "room-1",
        "events": [
            {
                "type": "agent_response",
                "agent_message_id": "msg-1",
                "data": {"task_id": "task-1", "content": "one"},
            },
            {
                "type": "agent_response",
                "agent_message_id": "msg-1",
                "data": {"task_id": "task-1", "content": "one"},
            },
        ],
    }
    await service.publish_from_hub("hub-1", payload)

    assert len(dispatcher.events) == 2
    assert dispatcher.events[0].journal_id != dispatcher.events[1].journal_id
    assert dispatcher.events[0].idempotency_key.startswith("ingest:")
    converted = hub_agent_response_internal_to_agent_event(dispatcher.events[0])
    assert converted.kind == "response"
    assert converted.message_id == "msg-1"
    assert converted.text == "one"


@pytest.mark.asyncio
async def test_publish_ignores_unsupported_legacy_event_types() -> None:
    journal = InMemoryHubResponseJournal()
    dispatcher = Dispatcher()
    service = HubPublishService(journal=journal, dispatcher=dispatcher)

    await service.publish_from_hub(
        "hub-1",
        {
            "room_id": "room-1",
            "events": [
                {
                    "type": "agent_token",
                    "agent_message_id": "msg-1",
                    "data": {"task_id": "task-1", "token": "hello"},
                }
            ],
        },
    )

    assert dispatcher.events == []
    assert await journal.find_replayable() == []


@pytest.mark.asyncio
async def test_publish_stable_response_seq_suppresses_duplicate_internal_dispatch() -> None:
    journal = InMemoryHubResponseJournal()
    sink = Sink()
    dispatcher = HubInternalResponseRouter(sink=sink, journal=journal, worker_id="worker-1")
    service = HubPublishService(journal=journal, dispatcher=dispatcher)
    event = {
        "room_id": "room-1",
        "events": [
            {
                "type": "task_status",
                "agent_message_id": "msg-1",
                "data": {"task_id": "task-1", "response_seq": 7},
            }
        ],
    }

    await service.publish_from_hub("hub-1", event)
    await service.publish_from_hub("hub-1", event)

    assert len(sink.events) == 1


@pytest.mark.asyncio
async def test_publish_stable_response_seq_retries_unprocessed_dispatch_failure() -> None:
    journal = InMemoryHubResponseJournal()

    class FailingOnceDispatcher:
        def __init__(self) -> None:
            self.events = []

        async def dispatch_hub_internal_response(self, event):
            self.events.append(event)
            if len(self.events) == 1:
                raise RuntimeError("transient")

    dispatcher = FailingOnceDispatcher()
    service = HubPublishService(journal=journal, dispatcher=dispatcher)
    event = {
        "room_id": "room-1",
        "events": [
            {
                "type": "task_status",
                "agent_message_id": "msg-1",
                "data": {"task_id": "task-1", "response_seq": 7},
            }
        ],
    }

    with pytest.raises(RuntimeError, match="transient"):
        await service.publish_from_hub("hub-1", event)
    await service.publish_from_hub("hub-1", event)

    assert len(dispatcher.events) == 2


@pytest.mark.asyncio
async def test_publish_claims_but_does_not_mark_processed_after_publisher_only_delivery() -> None:
    journal = InMemoryHubResponseJournal()
    publisher = EventPublisher()
    service = HubPublishService(
        journal=journal,
        event_publisher=publisher,
        worker_id="worker-1",
    )

    await service.publish_from_hub(
        "hub-1",
        {
            "room_id": "room-1",
            "events": [
                {
                    "type": "agent_response",
                    "agent_message_id": "msg-1",
                    "data": {"task_id": "task-1", "content": "ok"},
                }
            ],
        },
    )

    assert len(publisher.events) == 1
    assert publisher.events[0].claim_token
    await journal.release_claim(
        publisher.events[0].journal_id,
        publisher.events[0].claim_token,
    )
    assert len(await journal.find_replayable()) == 1


@pytest.mark.asyncio
async def test_publish_deduplicates_remote_fanout_while_journal_claim_is_active() -> None:
    journal = InMemoryHubResponseJournal()
    publisher = EventPublisher()
    service = HubPublishService(
        journal=journal,
        dispatcher=NoopDispatcher(),
        event_publisher=publisher,
        worker_id="worker-1",
    )
    event = {
        "room_id": "room-1",
        "events": [
            {
                "type": "task_status",
                "agent_message_id": "msg-1",
                "data": {"task_id": "task-1", "response_seq": 7},
            }
        ],
    }

    await service.publish_from_hub("hub-1", event)
    await service.publish_from_hub("hub-1", event)

    assert len(publisher.events) == 1


@pytest.mark.asyncio
async def test_publish_does_not_emit_after_direct_dispatch_processed_journal() -> None:
    journal = InMemoryHubResponseJournal()
    sink = Sink()
    dispatcher = HubInternalResponseRouter(
        sink=sink,
        journal=journal,
        worker_id="worker-1",
    )
    publisher = EventPublisher()
    service = HubPublishService(
        journal=journal,
        dispatcher=dispatcher,
        event_publisher=publisher,
    )

    await service.publish_from_hub(
        "hub-1",
        {
            "room_id": "room-1",
            "events": [
                {
                    "type": "agent_response",
                    "agent_message_id": "msg-1",
                    "data": {"task_id": "task-1", "content": "ok"},
                }
            ],
        },
    )

    assert len(sink.events) == 1
    assert publisher.events == []


@pytest.mark.asyncio
async def test_response_replay_worker_dispatches_claimed_journal_and_marks_processed() -> None:
    journal = InMemoryHubResponseJournal()
    record = await journal.create_or_get(
        {
            "hub_id": "hub-1",
            "room_id": "room-1",
            "agent_message_id": "msg-1",
            "task_id": "task-1",
            "event_type": "agent_response",
            "payload": {
                "kind": "response",
                "message_id": "msg-1",
                "task_id": "task-1",
                "text": "replayed",
            },
            "idempotency_key": "ingest:replay",
        }
    )

    sink = Sink()
    dispatcher = HubInternalResponseRouter(
        sink=sink,
        journal=journal,
        worker_id="worker-1",
    )
    worker = HubResponseReplayWorker(
        journal=journal,
        dispatcher=dispatcher,
        worker_id="worker-1",
    )

    assert await worker.replay_once() == 1
    assert len(sink.events) == 1
    assert sink.events[0].claim_token
    assert await journal.claim_for_processing(record["journal_id"], "worker-1") is None


@pytest.mark.asyncio
async def test_response_replay_worker_skips_live_remote_owner_before_claiming() -> None:
    journal = InMemoryHubResponseJournal()
    ownership = InMemoryHubTaskOwnershipStore()
    record = await journal.create_or_get(
        {
            "hub_id": "hub-1",
            "room_id": "room-1",
            "agent_message_id": "msg-1",
            "task_id": "task-1",
            "event_type": "agent_response",
            "payload": {
                "kind": "response",
                "message_id": "msg-1",
                "task_id": "task-1",
                "text": "remote",
            },
            "idempotency_key": "ingest:remote",
        }
    )
    await ownership.claim_or_refresh(
        {"agent_message_id": "msg-1", "local_task_id": "task-1"},
        "worker-2",
        "lease-1",
    )
    worker = HubResponseReplayWorker(
        journal=journal,
        dispatcher=Dispatcher(),
        worker_id="worker-1",
        ownership_store=ownership,
    )

    assert await worker.replay_once() == 0
    assert await journal.claim_for_processing(record["journal_id"], "worker-2")


@pytest.mark.asyncio
async def test_internal_response_router_releases_claim_after_sink_failure() -> None:
    journal = InMemoryHubResponseJournal()
    record = await journal.create_or_get(
        {
            "hub_id": "hub-1",
            "room_id": "room-1",
            "agent_message_id": "msg-1",
            "task_id": "task-1",
            "payload": {"kind": "response", "message_id": "msg-1"},
        }
    )

    class FailingSink:
        async def handle_hub_agent_response(self, event):
            raise RuntimeError("transient")

    router = HubInternalResponseRouter(
        sink=FailingSink(),
        journal=journal,
        worker_id="worker-1",
    )

    with pytest.raises(RuntimeError, match="transient"):
        await router.dispatch_hub_internal_response(
            type(
                "Event",
                (),
                {
                    "journal_id": record["journal_id"],
                    "task_id": "task-1",
                    "payload": {"message_id": "msg-1", "task_id": "task-1"},
                },
            )()
        )

    assert await journal.claim_for_processing(record["journal_id"], "worker-1")


@pytest.mark.asyncio
async def test_internal_response_router_skips_live_remote_owner() -> None:
    ownership = InMemoryHubTaskOwnershipStore()
    await ownership.claim_or_refresh(
        {"agent_message_id": "msg-1", "local_task_id": "task-1"},
        "worker-2",
        "lease-1",
    )
    sink = Sink()
    router = HubInternalResponseRouter(
        sink=sink,
        ownership_store=ownership,
        worker_id="worker-1",
    )

    await router.dispatch_hub_internal_response(
        type(
            "Event",
            (),
            {
                "task_id": "task-1",
                "payload": {"message_id": "msg-1", "task_id": "task-1"},
            },
        )()
    )

    assert sink.events == []


@pytest.mark.asyncio
async def test_internal_response_router_processes_expired_remote_owner() -> None:
    ownership = InMemoryHubTaskOwnershipStore()
    record = await ownership.claim_or_refresh(
        {"agent_message_id": "msg-1", "local_task_id": "task-1"},
        "worker-2",
        "lease-1",
    )
    ownership._records[record["ownership_id"]]["lease_expires_at"] = (
        utcnow() - timedelta(seconds=1)
    )
    sink = Sink()
    router = HubInternalResponseRouter(
        sink=sink,
        ownership_store=ownership,
        worker_id="worker-1",
    )

    await router.dispatch_hub_internal_response(
        type(
            "Event",
            (),
            {
                "task_id": "task-1",
                "payload": {"message_id": "msg-1", "task_id": "task-1"},
            },
        )()
    )

    assert len(sink.events) == 1


@pytest.mark.asyncio
async def test_publish_authorizes_each_event_and_skips_cancelled_lineage() -> None:
    lineage = HubPublishLineageSnapshot(
        room_id="room-1",
        room_owner_id="owner-1",
        agent_message_id="msg-1",
        agent_id="agent-1",
        agent_hub_id="hub-1",
        cancellation_message_ids=["msg-1", "user-1"],
    )
    auth = AuthorizationReader(lineage)
    dispatcher = Dispatcher()
    service = HubPublishService(
        journal=InMemoryHubResponseJournal(),
        dispatcher=dispatcher,
        publish_authorization_reader=auth,
        cancellation_reader=CancellationReader({"user-1"}),
    )

    await service.publish_from_hub(
        "hub-1",
        {
            "room_id": "room-1",
            "owner_id": "owner-1",
            "events": [
                {
                    "type": "agent_response",
                    "agent_message_id": "msg-1",
                    "data": {"task_id": "task-1", "content": "skip"},
                }
            ],
        },
    )

    assert auth.calls == [
        {
            "hub_id": "hub-1",
            "owner_id": "owner-1",
            "room_id": "room-1",
            "agent_message_id": "msg-1",
        }
    ]
    assert dispatcher.events == []


@pytest.mark.asyncio
async def test_publish_uses_authorized_lineage_agent_id_when_payload_omits_it() -> None:
    lineage = HubPublishLineageSnapshot(
        room_id="room-1",
        room_owner_id="owner-1",
        agent_message_id="msg-1",
        agent_id="agent-from-lineage",
        agent_hub_id="hub-1",
    )
    dispatcher = Dispatcher()
    service = HubPublishService(
        journal=InMemoryHubResponseJournal(),
        dispatcher=dispatcher,
        publish_authorization_reader=AuthorizationReader(lineage),
        cancellation_reader=CancellationReader(),
    )

    await service.publish_from_hub(
        "hub-1",
        {
            "room_id": "room-1",
            "owner_id": "owner-1",
            "events": [
                {
                    "type": "agent_response",
                    "agent_message_id": "msg-1",
                    "data": {"task_id": "task-1", "content": "ok"},
                }
            ],
        },
    )

    converted = hub_agent_response_internal_to_agent_event(dispatcher.events[0])
    assert converted.agent_id == "agent-from-lineage"


@pytest.mark.asyncio
async def test_publish_uses_tracked_task_id_from_lineage_when_payload_omits_task_id() -> None:
    lineage = HubPublishLineageSnapshot(
        room_id="room-1",
        room_owner_id="owner-1",
        agent_message_id="msg-1",
        agent_id="agent-1",
        agent_hub_id="hub-1",
        tracked_task_id="relay-pending-msg-1",
    )
    dispatcher = Dispatcher()
    service = HubPublishService(
        journal=InMemoryHubResponseJournal(),
        dispatcher=dispatcher,
        publish_authorization_reader=AuthorizationReader(lineage),
    )

    await service.publish_from_hub(
        "hub-1",
        {
            "room_id": "room-1",
            "owner_id": "owner-1",
            "events": [
                {
                    "type": "agent_response",
                    "agent_message_id": "msg-1",
                    "data": {"content": "ok"},
                }
            ],
        },
    )

    assert dispatcher.events[0].task_id == "relay-pending-msg-1"
    assert dispatcher.events[0].payload["task_id"] == "relay-pending-msg-1"


@pytest.mark.asyncio
async def test_publish_drops_processing_status_with_mismatched_lifecycle_id() -> None:
    lineage = HubPublishLineageSnapshot(
        room_id="room-1",
        room_owner_id="owner-1",
        agent_message_id="msg-1",
        agent_id="agent-1",
        agent_hub_id="hub-1",
        lifecycle_message_id="user-1",
        root_user_message_id="user-1",
    )
    dispatcher = Dispatcher()
    service = HubPublishService(
        journal=InMemoryHubResponseJournal(),
        dispatcher=dispatcher,
        publish_authorization_reader=AuthorizationReader(lineage),
    )

    await service.publish_from_hub(
        "hub-1",
        {
            "room_id": "room-1",
            "owner_id": "owner-1",
            "events": [
                {
                    "type": "processing_status",
                    "agent_message_id": "msg-1",
                    "data": {
                        "task_id": "task-1",
                        "status": "completed",
                        "user_message_id": "other-user",
                    },
                }
            ],
        },
    )

    assert dispatcher.events == []


@pytest.mark.asyncio
async def test_publish_authorized_lineage_overwrites_hub_supplied_agent_id() -> None:
    lineage = HubPublishLineageSnapshot(
        room_id="room-1",
        room_owner_id="owner-1",
        agent_message_id="msg-1",
        agent_id="agent-from-lineage",
        agent_hub_id="hub-1",
    )
    dispatcher = Dispatcher()
    service = HubPublishService(
        journal=InMemoryHubResponseJournal(),
        dispatcher=dispatcher,
        publish_authorization_reader=AuthorizationReader(lineage),
        cancellation_reader=CancellationReader(),
    )

    await service.publish_from_hub(
        "hub-1",
        {
            "room_id": "room-1",
            "owner_id": "owner-1",
            "events": [
                {
                    "type": "agent_response",
                    "agent_message_id": "msg-1",
                    "data": {
                        "task_id": "task-1",
                        "agent_id": "spoofed",
                        "content": "ok",
                    },
                }
            ],
        },
    )

    assert dispatcher.events[0].agent_id == "agent-from-lineage"


@pytest.mark.asyncio
async def test_publish_skips_unauthorized_event_before_journal_write() -> None:
    dispatcher = Dispatcher()
    service = HubPublishService(
        journal=InMemoryHubResponseJournal(),
        dispatcher=dispatcher,
        publish_authorization_reader=AuthorizationReader(None),
    )

    await service.publish_from_hub(
        "hub-1",
        {
            "room_id": "room-1",
            "owner_id": "owner-1",
            "events": [
                {
                    "type": "agent_response",
                    "agent_message_id": "msg-1",
                    "data": {"task_id": "task-1", "content": "bad"},
                }
            ],
        },
    )

    assert dispatcher.events == []


@pytest.mark.asyncio
async def test_publish_terminal_task_status_maps_to_terminal_agent_event() -> None:
    dispatcher = Dispatcher()
    service = HubPublishService(
        journal=InMemoryHubResponseJournal(),
        dispatcher=dispatcher,
    )

    await service.publish_from_hub(
        "hub-1",
        {
            "room_id": "room-1",
            "events": [
                {
                    "type": "task_status",
                    "agent_message_id": "msg-1",
                    "data": {
                        "task_id": "task-1",
                        "state": "completed",
                        "status_text": "done",
                    },
                }
            ],
        },
    )

    converted = hub_agent_response_internal_to_agent_event(dispatcher.events[0])
    assert converted.kind == "response"
    assert converted.is_final is True
    assert converted.state == "completed"


@pytest.mark.asyncio
async def test_publish_artifact_update_preserves_artifact_payload() -> None:
    dispatcher = Dispatcher()
    service = HubPublishService(
        journal=InMemoryHubResponseJournal(),
        dispatcher=dispatcher,
    )

    await service.publish_from_hub(
        "hub-1",
        {
            "room_id": "room-1",
            "events": [
                {
                    "type": "artifact_update",
                    "agent_message_id": "msg-1",
                    "data": {
                        "task_id": "task-1",
                        "artifact": {"parts": [{"text": "file text"}]},
                    },
                }
            ],
        },
    )

    converted = hub_agent_response_internal_to_agent_event(dispatcher.events[0])
    assert converted.kind == "artifact_update"
    assert converted.artifacts == [{"parts": [{"kind": "text", "text": "file text"}]}]


@pytest.mark.asyncio
async def test_publish_artifact_update_preserves_file_part_metadata() -> None:
    dispatcher = Dispatcher()
    service = HubPublishService(
        journal=InMemoryHubResponseJournal(),
        dispatcher=dispatcher,
    )

    await service.publish_from_hub(
        "hub-1",
        {
            "room_id": "room-1",
            "events": [
                {
                    "type": "artifact_update",
                    "agent_message_id": "msg-1",
                    "data": {
                        "task_id": "task-1",
                        "artifact": {
                            "parts": [
                                {
                                    "raw": "abc",
                                    "mediaType": "text/plain",
                                    "filename": "a.txt",
                                    "metadata": {"k": "v"},
                                }
                            ]
                        },
                    },
                }
            ],
        },
    )

    converted = hub_agent_response_internal_to_agent_event(dispatcher.events[0])
    assert converted.artifacts == [
        {
            "parts": [
                {
                    "kind": "file",
                    "file": {
                        "bytes": "abc",
                        "mimeType": "text/plain",
                        "name": "a.txt",
                    },
                    "metadata": {"k": "v"},
                }
            ]
        }
    ]
