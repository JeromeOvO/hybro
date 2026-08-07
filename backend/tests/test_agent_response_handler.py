"""
Integration tests for AgentResponseHandler — parity verification.

Tests that feeding the same AgentEvent sequences through the handler
produces the expected persistence and public delivery effects for each event kind,
and that flow-control flags (skip_persist) work.
"""

import ast
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from execution.dispatch.agent_event import AgentEvent
from execution.dispatch.response_handler import (
    AgentResponseHandler,
    ResponseTaskWriter,
    bind_orchestration_result_ingestor,
)
from execution.orchestration.result_ingestor import AgentResultIngestor, AgentResultRead
from models.orchestration import OrchestrationRunState

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def _reset_orchestration_result_ingestor():
    bind_orchestration_result_ingestor(None)
    yield
    bind_orchestration_result_ingestor(None)


def _make_handler(
    *,
    db=None,
    sse=None,
    rmc=None,
    hitl_coordinator=None,
    slot_lifecycle=None,
    task_notifier=None,
    task_notification_impl=None,
    task_notification_store=None,
):
    if db is None:
        db = MagicMock()
        db.update_task_state_on_message = AsyncMock(return_value=(True, None))
        db.accumulate_artifact_on_message = AsyncMock(return_value=True)
        db.get_pending_continuation_on_message = AsyncMock(return_value=None)
    if sse is None:
        sse = MagicMock()
        sse.send_agent_response = AsyncMock()
        sse.send_artifact_update = AsyncMock()
        sse.send_task_submitted = AsyncMock()
        sse.send_task_update = AsyncMock()
        sse.send_processing_status = AsyncMock()
        sse.send_error = AsyncMock()
    if rmc is None:
        rmc = MagicMock()
        rmc.resume_queue_from_continuation = AsyncMock(return_value=True)
    if task_notifier is None:
        task_notifier = MagicMock()
    kwargs = {
        "message_writer": db,
        "task_writer": db,
        "continuation_store": db,
        "client_request_resolver": db,
        "room_reader": db,
        "hitl_reader": db,
        "delivery": sse,
        "room_message_center": rmc,
        "slot_lifecycle": slot_lifecycle,
        "hitl_coordinator": hitl_coordinator,
        "task_notifier": task_notifier,
        "task_notification_impl": task_notification_impl,
    }
    if task_notification_store is not None:
        kwargs["task_notification_store"] = task_notification_store
    return AgentResponseHandler(**kwargs)


def _base_event(**overrides):
    defaults = dict(
        message_id="msg-001",
        room_id="room-001",
        agent_id="agent-001",
        user_id="user-001",
        related_message_id="umsg-001",
    )
    defaults.update(overrides)
    return defaults


@pytest.mark.asyncio
async def test_completed_journal_replay_is_acknowledged_without_repeating_side_effects():
    db = MagicMock()
    db.begin_terminal_finalization = AsyncMock(return_value=None)
    db.terminal_finalization_matches = AsyncMock(return_value=True)
    db.update_task_state_on_message = AsyncMock(return_value=(False, None))
    db.accumulate_artifact_on_message = AsyncMock(return_value=True)
    db.get_pending_continuation_on_message = AsyncMock(return_value=None)
    delivery = MagicMock()
    delivery.send_agent_response = AsyncMock()
    handler = _make_handler(db=db, sse=delivery)

    await handler.handle(
        AgentEvent(
            kind="response",
            **_base_event(),
            text="already committed",
            retry_on_finalization_conflict=True,
            finalization_recovery_id="journal-1",
        )
    )

    db.terminal_finalization_matches.assert_awaited_once_with(
        "msg-001",
        "completed",
        recovery_source="journal",
        recovery_id="journal-1",
    )
    db.update_task_state_on_message.assert_not_awaited()
    delivery.send_agent_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_active_journal_finalization_conflict_remains_retryable():
    db = MagicMock()
    db.begin_terminal_finalization = AsyncMock(return_value=None)
    db.terminal_finalization_matches = AsyncMock(return_value=False)
    handler = _make_handler(db=db)

    with pytest.raises(RuntimeError, match="already in progress"):
        await handler.handle(
            AgentEvent(
                kind="response",
                **_base_event(),
                retry_on_finalization_conflict=True,
                finalization_recovery_id="journal-1",
            )
        )


def test_processing_status_callback_has_no_required_post_emit_business_side_effects():
    path = (
        Path(__file__).resolve().parents[1]
        / "execution"
        / "dispatch"
        / "response_handler.py"
    )
    tree = ast.parse(path.read_text(), filename=str(path))
    fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_on_processing_status"
    )
    emit_lines = [
        node.lineno
        for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_emit_processing_status"
    ]
    assert emit_lines
    last_emit = max(emit_lines)
    forbidden_after_emit = {
        "record_and_maybe_emit_run_event",
        "update_task_state_on_message",
        "accumulate_artifact_on_message",
        "resume_queue_from_continuation",
        "request_input",
    }
    post_emit = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call) or node.lineno <= last_emit:
            continue
        if isinstance(node.func, ast.Attribute):
            name = node.func.attr
        elif isinstance(node.func, ast.Name):
            name = node.func.id
        else:
            name = None
        if name in forbidden_after_emit:
            post_emit.append((node.lineno, ast.unparse(node)))
    assert post_emit == []


# =============================================================================
# Artifact update events
# =============================================================================


class TestArtifactUpdateEvent:
    @pytest.mark.asyncio
    async def test_append_false_replacement_excludes_old_artifact_from_budget(self):
        existing = [
            {
                "artifactId": "artifact-1",
                "parts": [
                    {
                        "kind": "file",
                        "file": {
                            "uri": f"/api/v1/files/old-{index}/content",
                            "name": f"old-{index}.bin",
                            "mimeType": "application/octet-stream",
                        },
                        "metadata": {
                            "file_id": f"old-{index}",
                            "size_bytes": 5 * 1024 * 1024,
                            "sha256": f"old-sha-{index}",
                        },
                    }
                    for index in range(20)
                ],
            }
        ]
        db = MagicMock()
        db.accumulate_artifact_on_message = AsyncMock(return_value=True)
        handler = _make_handler(db=db)
        handler._existing_artifact_journal = AsyncMock(side_effect=[existing, existing])
        event = AgentEvent(
            kind="artifact_update",
            **_base_event(),
            artifacts=[
                {
                    "artifactId": "artifact-1",
                    "parts": [
                        {
                            "kind": "file",
                            "file": {
                                "bytes": "bmV3",
                                "name": "new.bin",
                                "mimeType": "application/octet-stream",
                            },
                        }
                    ],
                }
            ],
            append=False,
        )

        observed_budget = {}

        async def materialize(parts, *args, **kwargs):
            del args
            observed_budget.update(kwargs["budget"])
            parts[0] = {"kind": "data", "data": {"type": "replacement"}}
            return 0

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "common.utils.a2a_helpers.materialize_inline_file_parts",
                materialize,
            )
            mp.setattr(
                "common.utils.a2a_helpers.delete_superseded_agent_artifacts",
                AsyncMock(),
            )
            await handler._process_artifact(event)

        assert observed_budget["attempted"] == 0
        assert observed_budget["raw"] == 0

    @pytest.mark.asyncio
    async def test_terminal_append_false_replacement_uses_retained_budget(self):
        existing = [
            {
                "artifactId": "artifact-1",
                "parts": [
                    {
                        "kind": "file",
                        "file": {
                            "uri": f"/api/v1/files/old-{index}/content",
                            "name": f"old-{index}.bin",
                            "mimeType": "application/octet-stream",
                        },
                        "metadata": {
                            "file_id": f"old-{index}",
                            "size_bytes": 5 * 1024 * 1024,
                            "sha256": f"old-sha-{index}",
                        },
                    }
                    for index in range(20)
                ],
            }
        ]
        db = MagicMock()
        db.get_room_agent_message_by_message_id = AsyncMock(
            return_value=SimpleNamespace(
                message_content=SimpleNamespace(
                    message_task=SimpleNamespace(artifacts=existing)
                )
            )
        )
        handler = _make_handler(db=db)
        storage = MagicMock()
        storage.content_url.return_value = "/api/v1/files/new-file/content"
        storage.store_agent_artifact = AsyncMock(
            return_value={
                "file_id": "new-file",
                "file_name": "new.bin",
                "mime_type": "application/octet-stream",
                "size_bytes": 3,
                "sha256": "new-sha",
            }
        )

        from a2a_adapter import artifact_storage
        from common.utils.a2a_helpers import bind_a2a_artifact_files

        artifact_storage.bind_artifact_files(storage)
        bind_a2a_artifact_files(artifact_storage)
        event = AgentEvent(
            kind="response",
            **_base_event(),
            artifacts=[
                {
                    "artifactId": "artifact-1",
                    "parts": [
                        {
                            "kind": "file",
                            "file": {
                                "bytes": "bmV3",
                                "name": "new.bin",
                                "mimeType": "application/octet-stream",
                            },
                        }
                    ],
                }
            ],
            append=False,
        )

        _text, artifacts, _delivery_failed = await handler._project_completed_output(
            event
        )

        storage.store_agent_artifact.assert_awaited_once()
        assert artifacts is not None
        assert artifacts[0]["parts"][0]["metadata"]["file_id"] == "new-file"

    @pytest.mark.asyncio
    async def test_append_false_deletes_superseded_agent_file_after_journal_replace(
        self,
    ):
        old_part = {
            "kind": "file",
            "file": {
                "uri": "/api/v1/files/old-file/content",
                "name": "old.txt",
                "mimeType": "text/plain",
            },
            "metadata": {
                "file_id": "old-file",
                "file_name": "old.txt",
                "mime_type": "text/plain",
                "size_bytes": 3,
                "sha256": "old-sha",
            },
        }
        db = MagicMock()
        db.accumulate_artifact_on_message = AsyncMock(return_value=True)
        handler = _make_handler(db=db)
        handler._existing_artifact_journal = AsyncMock(
            side_effect=[
                [
                    {
                        "artifactId": "artifact-1",
                        "parts": [old_part],
                    }
                ],
                [
                    {
                        "artifactId": "artifact-1",
                        "parts": [
                            {
                                "kind": "file",
                                "file": {
                                    "uri": "/api/v1/files/new-file/content",
                                    "name": "new.txt",
                                    "mimeType": "text/plain",
                                },
                                "metadata": {
                                    "file_id": "new-file",
                                    "file_name": "new.txt",
                                    "mime_type": "text/plain",
                                    "size_bytes": 3,
                                    "sha256": "new-sha",
                                },
                            }
                        ],
                    }
                ],
            ]
        )
        event = AgentEvent(
            kind="artifact_update",
            **_base_event(),
            artifacts=[
                {
                    "artifactId": "artifact-1",
                    "parts": [
                        {
                            "kind": "file",
                            "file": {
                                "bytes": "bmV3",
                                "name": "new.txt",
                                "mimeType": "text/plain",
                            },
                        }
                    ],
                }
            ],
            append=False,
        )

        async def materialize(parts, *args, **kwargs):
            del args, kwargs
            parts[0] = {
                "kind": "file",
                "file": {
                    "uri": "/api/v1/files/new-file/content",
                    "name": "new.txt",
                    "mimeType": "text/plain",
                },
                "metadata": {
                    "file_id": "new-file",
                    "file_name": "new.txt",
                    "mime_type": "text/plain",
                    "size_bytes": 3,
                    "sha256": "new-sha",
                },
            }
            return 1

        with pytest.MonkeyPatch.context() as mp:
            cleanup = AsyncMock()
            mp.setattr(
                "common.utils.a2a_helpers.materialize_inline_file_parts",
                materialize,
            )
            mp.setattr(
                "common.utils.a2a_helpers.delete_superseded_agent_artifacts",
                cleanup,
            )
            await handler._process_artifact(event)

        cleanup.assert_awaited_once_with(
            room_id="room-001",
            message_id="msg-001",
            file_ids={"old-file"},
        )

    @pytest.mark.asyncio
    async def test_append_false_keeps_file_referenced_by_another_artifact(self):
        shared_part = {
            "kind": "file",
            "file": {
                "uri": "/api/v1/files/shared-file/content",
                "name": "shared.txt",
                "mimeType": "text/plain",
            },
            "metadata": {
                "file_id": "shared-file",
                "file_name": "shared.txt",
                "mime_type": "text/plain",
                "size_bytes": 6,
                "sha256": "shared-sha",
            },
        }
        existing = [
            {"artifactId": "artifact-1", "parts": [deepcopy(shared_part)]},
            {"artifactId": "artifact-2", "parts": [deepcopy(shared_part)]},
        ]
        committed = [
            {
                "artifactId": "artifact-1",
                "parts": [
                    {
                        "kind": "file",
                        "file": {
                            "uri": "/api/v1/files/new-file/content",
                            "name": "new.txt",
                            "mimeType": "text/plain",
                        },
                        "metadata": {
                            "file_id": "new-file",
                            "file_name": "new.txt",
                            "mime_type": "text/plain",
                            "size_bytes": 3,
                            "sha256": "new-sha",
                        },
                    }
                ],
            },
            existing[1],
        ]
        db = MagicMock()
        db.accumulate_artifact_on_message = AsyncMock(return_value=True)
        handler = _make_handler(db=db)
        handler._existing_artifact_journal = AsyncMock(
            side_effect=[existing, committed]
        )
        event = AgentEvent(
            kind="artifact_update",
            **_base_event(),
            artifacts=[
                {
                    "artifactId": "artifact-1",
                    "parts": [
                        {
                            "kind": "file",
                            "file": {
                                "bytes": "bmV3",
                                "name": "new.txt",
                                "mimeType": "text/plain",
                            },
                        }
                    ],
                }
            ],
            append=False,
        )

        async def materialize(parts, *args, **kwargs):
            del args, kwargs
            parts[:] = deepcopy(committed[0]["parts"])
            return 1

        with pytest.MonkeyPatch.context() as mp:
            cleanup = AsyncMock()
            mp.setattr(
                "common.utils.a2a_helpers.materialize_inline_file_parts",
                materialize,
            )
            mp.setattr(
                "common.utils.a2a_helpers.delete_superseded_agent_artifacts",
                cleanup,
            )
            await handler._process_artifact(event)

        cleanup.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_hub_artifact_update_journals_without_public_side_effects(
        self,
    ):
        """Nonterminal artifacts never cross the public response boundary."""
        private_text = "PRIVATE_SENTINEL_stream_text"
        private_bytes = "PRIVATE_SENTINEL_stream_file_bytes"
        private_metadata = "PRIVATE_SENTINEL_stream_metadata"
        service = MagicMock()
        service.ingest_agent_result = AsyncMock(return_value=None)
        bind_orchestration_result_ingestor(service)
        h = _make_handler()
        parts = [
            {
                "kind": "file",
                "file": {
                    "bytes": private_bytes,
                    "mime_type": "text/plain",
                    "name": "private.txt",
                },
                "metadata": {"private": private_metadata},
            }
        ]
        artifact = {
            "artifactId": "a-private",
            "name": "partial",
            "metadata": {"private": private_metadata},
            "parts": [
                {
                    "kind": "text",
                    "text": private_text,
                    "metadata": {"private": private_metadata},
                },
                {
                    "kind": "file",
                    "file": {
                        "bytes": private_bytes,
                        "mime_type": "text/plain",
                        "name": "private.txt",
                    },
                    "metadata": {"private": private_metadata},
                },
            ],
        }
        original_parts = deepcopy(parts)
        original_artifacts = deepcopy([artifact])
        event = AgentEvent(
            kind="artifact_update",
            **_base_event(),
            text=private_text,
            parts=parts,
            artifacts=[artifact],
            append=True,
            last_chunk=True,
            client_request_id="client-private",
        )

        with pytest.MonkeyPatch.context() as mp:
            mock_convert = AsyncMock(return_value=1)
            mp.setattr(
                "common.utils.a2a_helpers.materialize_inline_file_parts",
                mock_convert,
            )
            await h.handle(event)

        mock_convert.assert_awaited_once()
        h._task_writer.update_task_state_on_message.assert_not_awaited()
        h._message_writer.accumulate_artifact_on_message.assert_awaited_once()
        h._delivery.send_artifact_update.assert_not_awaited()
        h._delivery.send_agent_response.assert_not_awaited()
        h._delivery.send_task_update.assert_not_awaited()
        h._rmc.resume_queue_from_continuation.assert_not_awaited()
        service.ingest_agent_result.assert_not_awaited()
        assert event.text == private_text
        assert event.parts == original_parts
        assert event.artifacts == original_artifacts
        assert event.append is True
        assert event.last_chunk is True

    @pytest.mark.asyncio
    async def test_recovery_budget_does_not_charge_existing_durable_files_twice(
        self,
    ):
        parts = [
            {
                "kind": "file",
                "file": {
                    "uri": f"/api/v1/files/file-{index}/content",
                    "name": f"{index}.bin",
                    "mimeType": "application/octet-stream",
                },
                "metadata": {
                    "file_id": f"file-{index}",
                    "size_bytes": 1,
                    "sha256": f"sha-{index}",
                },
            }
            for index in range(20)
        ]
        existing = [
            {
                "artifactId": "artifact-1",
                "parts": parts,
            }
        ]
        db = MagicMock()
        db.get_room_agent_message_by_message_id = AsyncMock(
            return_value=SimpleNamespace(
                message_content=SimpleNamespace(
                    message_task=SimpleNamespace(artifacts=existing)
                )
            )
        )
        handler = _make_handler(db=db)
        budget = await handler._artifact_budget_from_journal("msg-001")
        storage = MagicMock()
        storage.content_url.side_effect = lambda file_id: (
            f"/api/v1/files/{file_id}/content"
        )

        async def validate_reference(**kwargs):
            file_id = kwargs["file_id"]
            index = file_id.removeprefix("file-")
            return {
                "file_id": file_id,
                "file_name": f"{index}.bin",
                "mime_type": "application/octet-stream",
                "size_bytes": 1,
                "sha256": f"sha-{index}",
                "status": "ready",
            }

        storage.validate_agent_reference = AsyncMock(side_effect=validate_reference)

        from a2a_adapter.artifact_storage import (
            bind_artifact_files,
            materialize_inline_file_parts,
        )

        bind_artifact_files(storage)
        replayed_parts = deepcopy(existing[0]["parts"]) + [
            {
                "kind": "file",
                "file": {
                    "bytes": "bmV3",
                    "name": "new.bin",
                    "mimeType": "application/octet-stream",
                },
            }
        ]
        await materialize_inline_file_parts(
            replayed_parts,
            "room-001",
            "msg-001",
            budget=budget,
            artifact_slot="id:artifact-1",
        )

        assert all(part["kind"] == "file" for part in replayed_parts[:20])
        assert replayed_parts[20]["data"]["type"] == "file_unavailable"
        assert replayed_parts[20]["data"]["reason"] == "size_limit"
        assert budget["converted"] == 20
        assert budget["raw"] == 20
        assert budget["precounted_file_ids"] == {}

    @pytest.mark.asyncio
    async def test_terminal_top_level_parts_share_durable_journal_budget(self):
        existing = [
            {
                "artifactId": "artifact-1",
                "parts": [
                    {
                        "kind": "file",
                        "file": {
                            "uri": f"/api/v1/files/file-{index}/content",
                            "name": f"{index}.bin",
                            "mimeType": "application/octet-stream",
                        },
                        "metadata": {
                            "file_id": f"file-{index}",
                            "size_bytes": 1,
                            "sha256": f"sha-{index}",
                        },
                    }
                    for index in range(20)
                ],
            }
        ]
        db = MagicMock()
        db.get_room_agent_message_by_message_id = AsyncMock(
            return_value=SimpleNamespace(
                message_content=SimpleNamespace(
                    message_task=SimpleNamespace(artifacts=existing)
                )
            )
        )
        handler = _make_handler(db=db)
        storage = MagicMock()
        storage.content_url.side_effect = lambda file_id: (
            f"/api/v1/files/{file_id}/content"
        )
        storage.store_agent_artifact = AsyncMock(
            return_value={
                "file_id": "new-file",
                "file_name": "new.bin",
                "mime_type": "application/octet-stream",
                "size_bytes": 3,
                "sha256": "new-sha",
            }
        )

        from a2a_adapter import artifact_storage
        from common.utils.a2a_helpers import bind_a2a_artifact_files

        artifact_storage.bind_artifact_files(storage)
        bind_a2a_artifact_files(artifact_storage)
        event = AgentEvent(
            kind="response",
            **_base_event(),
            parts=[
                {
                    "kind": "file",
                    "file": {
                        "bytes": "bmV3",
                        "name": "new.bin",
                        "mimeType": "application/octet-stream",
                    },
                }
            ],
        )

        _text, artifacts, _delivery_failed = await handler._project_completed_output(
            event
        )

        storage.store_agent_artifact.assert_not_awaited()
        assert artifacts is not None
        assert artifacts[-1]["parts"][0]["kind"] == "data"
        assert artifacts[-1]["parts"][0]["data"]["reason"] == "size_limit"

    @pytest.mark.asyncio
    async def test_terminal_file_only_materialization_failure_is_reported(self):
        db = MagicMock()
        db.get_room_agent_message_by_message_id = AsyncMock(return_value=None)
        handler = _make_handler(db=db)
        event = AgentEvent(
            kind="response",
            **_base_event(),
            artifacts=[
                {
                    "artifactId": "artifact-1",
                    "parts": [
                        {
                            "kind": "file",
                            "file": {
                                "bytes": "not-base64",
                                "name": "image.png",
                                "mimeType": "image/png",
                            },
                        }
                    ],
                }
            ],
        )

        _text, artifacts, delivery_failed = await handler._project_completed_output(
            event
        )

        assert delivery_failed is True
        assert event.details == {"output_failure_code": "artifact_delivery_failed"}
        assert artifacts is not None
        assert artifacts[0]["parts"][0]["data"]["type"] == "file_unavailable"

    @pytest.mark.asyncio
    async def test_unavailable_only_response_uses_failed_platform_terminal_state(self):
        db = MagicMock()
        db.update_task_state_on_message = AsyncMock(return_value=(True, None))
        db.accumulate_artifact_on_message = AsyncMock(return_value=True)
        db.get_pending_continuation_on_message = AsyncMock(return_value=None)
        delivery = MagicMock()
        notification_impl = AsyncMock(return_value=True)
        handler = _make_handler(
            db=db,
            sse=delivery,
            task_notification_impl=notification_impl,
            task_notification_store=MagicMock(),
        )

        await handler.handle(
            AgentEvent(
                kind="response",
                **_base_event(),
                artifacts=[
                    {
                        "artifactId": "artifact-1",
                        "parts": [
                            {
                                "kind": "file",
                                "file": {
                                    "bytes": "not-base64",
                                    "name": "image.png",
                                },
                            }
                        ],
                    }
                ],
            )
        )

        persist = db.update_task_state_on_message.await_args
        assert persist.args[:2] == ("msg-001", "failed")
        assert persist.kwargs["task_metadata"] == {
            "output_failure_code": "artifact_delivery_failed",
            "remote_task_state": "completed",
        }
        notified = notification_impl.await_args.kwargs
        assert str(getattr(notified["state"], "value", notified["state"])) == "failed"
        assert notified["error"] == "Agent output could not be processed."

    @pytest.mark.asyncio
    async def test_text_only_artifact_update_is_dropped_without_synthetic_artifact(
        self,
    ):
        h = _make_handler()
        event = AgentEvent(
            kind="artifact_update",
            **_base_event(),
            text="chunk",
            artifacts=None,
            append=True,
            last_chunk=True,
        )
        await h.handle(event)
        h._task_writer.update_task_state_on_message.assert_not_awaited()
        h._message_writer.accumulate_artifact_on_message.assert_not_awaited()
        h._delivery.send_artifact_update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_file_artifact_update_materialization_failure_is_not_public(self):
        h = _make_handler()
        artifact = {
            "artifactId": "a1",
            "parts": [
                {
                    "kind": "file",
                    "file": {"bytes": "dGVzdA==", "mime_type": "text/plain"},
                }
            ],
        }
        event = AgentEvent(
            kind="artifact_update",
            **_base_event(),
            artifacts=[artifact],
        )

        with pytest.MonkeyPatch.context() as mp:
            mock_convert = AsyncMock(side_effect=RuntimeError("files unavailable"))
            mp.setattr(
                "common.utils.a2a_helpers.materialize_inline_file_parts",
                mock_convert,
            )
            await h.handle(event)

        mock_convert.assert_awaited_once()
        h._message_writer.accumulate_artifact_on_message.assert_not_awaited()
        h._delivery.send_artifact_update.assert_not_awaited()


# =============================================================================
# Response events (terminal)
# =============================================================================


class TestResponseEvent:
    @pytest.mark.asyncio
    async def test_completed_response_does_not_replace_artifacts_when_journal_read_fails(
        self,
    ):
        db = MagicMock()
        db.get_room_agent_message_by_message_id = AsyncMock(
            side_effect=RuntimeError("temporary Mongo read failure")
        )
        db.update_task_state_on_message = AsyncMock(return_value=(True, None))
        db.get_pending_continuation_on_message = AsyncMock(return_value=None)
        handler = _make_handler(db=db)

        with pytest.raises(RuntimeError, match="temporary Mongo read failure"):
            await handler.handle(
                AgentEvent(
                    kind="response",
                    **_base_event(),
                    text="Visible final answer",
                )
            )

        db.update_task_state_on_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_persists_completed_and_resumes(self):
        h = _make_handler()
        event = AgentEvent(kind="response", **_base_event(), text="Done!")

        with pytest.MonkeyPatch.context() as mp:
            mock_notify = AsyncMock(return_value=True)
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                mock_notify,
            )
            await h.handle(event)

        h._message_writer.update_task_state_on_message.assert_awaited_once_with(
            "msg-001",
            "completed",
            message_text="Done!",
            artifacts=[
                {
                    "artifactId": "msg-001-response",
                    "name": "response",
                    "parts": [{"kind": "text", "text": "Done!"}],
                }
            ],
        )
        h._rmc.resume_queue_from_continuation.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_text_only_completed_response_materializes_public_artifact_for_refresh(
        self,
    ):
        h = _make_handler()
        event = AgentEvent(
            kind="response", **_base_event(), text="Visible final answer"
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                AsyncMock(return_value=True),
            )
            await h.handle(event)

        kwargs = h._message_writer.update_task_state_on_message.await_args.kwargs
        assert kwargs["message_text"] == "Visible final answer"
        assert kwargs["artifacts"] == [
            {
                "artifactId": "msg-001-response",
                "name": "response",
                "parts": [{"kind": "text", "text": "Visible final answer"}],
            }
        ]

    @pytest.mark.asyncio
    async def test_completed_response_keeps_public_text_beside_data_artifact(self):
        h = _make_handler()
        event = AgentEvent(
            kind="response",
            **_base_event(),
            text="raw transport text",
            public_text="The agent completed the request.",
            artifacts=[
                {
                    "artifactId": "submission-1",
                    "name": "cyber_submission",
                    "parts": [{"kind": "data", "data": {"company": "Acme SaaS Inc."}}],
                }
            ],
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                AsyncMock(return_value=True),
            )
            await h.handle(event)

        kwargs = h._message_writer.update_task_state_on_message.await_args.kwargs
        assert kwargs["message_text"] == "The agent completed the request."
        assert kwargs["artifacts"][0]["name"] == "cyber_submission"
        assert kwargs["artifacts"][0]["parts"][0]["data"] == {
            "company": "Acme SaaS Inc."
        }

    @pytest.mark.asyncio
    async def test_completed_response_sanitizes_artifacts_for_all_terminal_consumers(
        self,
    ):
        private_bytes = "PRIVATE_SENTINEL_completed_file_bytes"
        private_metadata = "PRIVATE_SENTINEL_completed_metadata"
        h = _make_handler(
            slot_lifecycle=MagicMock(terminate_slot=AsyncMock()),
        )
        service = MagicMock()
        service.ingest_agent_result = AsyncMock(return_value=None)
        bind_orchestration_result_ingestor(service)
        event = AgentEvent(
            kind="response",
            **_base_event(turn_id="turn-001"),
            text="raw status text should not be used",
            artifacts=[
                {
                    "artifactId": "artifact-private",
                    "name": "response",
                    "metadata": {"private": private_metadata},
                    "parts": [
                        {
                            "kind": "text",
                            "text": "Visible completed output",
                            "metadata": {"private": private_metadata},
                        },
                        {
                            "kind": "file",
                            "file": {
                                "bytes": private_bytes,
                                "mime_type": "text/plain",
                                "name": "private.txt",
                            },
                            "metadata": {"private": private_metadata},
                        },
                    ],
                }
            ],
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "common.utils.a2a_helpers.materialize_inline_file_parts",
                AsyncMock(side_effect=RuntimeError("S3 unavailable")),
            )
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                AsyncMock(return_value=True),
            )
            await h.handle(event)

        persisted_kwargs = h._task_writer.update_task_state_on_message.await_args.kwargs
        assert persisted_kwargs["message_text"] == "Visible completed output"
        payloads = [
            persisted_kwargs["artifacts"],
            h._slot_lifecycle.terminate_slot.await_args.kwargs["artifacts"],
            service.ingest_agent_result.await_args.args[0].artifacts,
        ]
        for payload in payloads:
            payload_json = json.dumps(payload, sort_keys=True)
            assert private_bytes not in payload_json
            assert private_metadata not in payload_json
            assert "Visible completed output" in payload_json

    @pytest.mark.asyncio
    async def test_completed_response_does_not_fallback_to_raw_text_when_artifacts_drop(
        self,
    ):
        private_text = "PRIVATE_SENTINEL_completed_status_text"
        private_bytes = "PRIVATE_SENTINEL_completed_only_file_bytes"
        h = _make_handler()
        event = AgentEvent(
            kind="response",
            **_base_event(),
            text=private_text,
            artifacts=[
                {
                    "artifactId": "unsafe-file-only",
                    "parts": [
                        {
                            "kind": "file",
                            "file": {
                                "bytes": private_bytes,
                                "mime_type": "text/plain",
                                "name": "private.txt",
                            },
                        }
                    ],
                }
            ],
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "common.utils.a2a_helpers.materialize_inline_file_parts",
                AsyncMock(side_effect=RuntimeError("S3 unavailable")),
            )
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                AsyncMock(return_value=True),
            )
            await h.handle(event)

        call = h._task_writer.update_task_state_on_message.await_args
        assert call.args[1] == "failed"
        assert call.kwargs["message_text"] is None
        assert call.kwargs["artifacts"][0]["parts"][0]["data"]["type"] == (
            "file_unavailable"
        )
        assert call.kwargs["task_metadata"]["output_failure_code"] == (
            "artifact_delivery_failed"
        )
        assert private_text not in repr(call)
        assert private_bytes not in repr(call)

    @pytest.mark.asyncio
    async def test_parts_only_completed_response_drops_unaddressable_file(self):
        private_text = "PRIVATE_SENTINEL_parts_only_status_text"
        private_bytes = "PRIVATE_SENTINEL_parts_only_file_bytes"
        h = _make_handler()
        event = AgentEvent(
            kind="response",
            **_base_event(),
            text=private_text,
            parts=[
                {
                    "kind": "file",
                    "file": {
                        "bytes": private_bytes,
                        "mime_type": "text/plain",
                        "name": "private.txt",
                    },
                }
            ],
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "common.utils.a2a_helpers.materialize_inline_file_parts",
                AsyncMock(side_effect=RuntimeError("S3 unavailable")),
            )
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                AsyncMock(return_value=True),
            )
            await h.handle(event)

        call = h._task_writer.update_task_state_on_message.await_args
        assert call.args[1] == "failed"
        assert call.kwargs["message_text"] is None
        assert call.kwargs["artifacts"][0]["parts"][0]["data"]["type"] == (
            "file_unavailable"
        )
        assert call.kwargs["task_metadata"]["output_failure_code"] == (
            "artifact_delivery_failed"
        )
        assert private_text not in repr(call)
        assert private_bytes not in repr(call)

    @pytest.mark.asyncio
    async def test_terminal_notification_failure_does_not_block_response_cleanup(self):
        slot_lifecycle = MagicMock()
        slot_lifecycle.terminate_slot = AsyncMock()
        h = _make_handler(slot_lifecycle=slot_lifecycle)
        event = AgentEvent(
            kind="response",
            **_base_event(),
            text="Done!",
            turn_id="turn-001",
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                AsyncMock(side_effect=RuntimeError("notification store missing read")),
            )
            await h.handle(event)

        h._message_writer.update_task_state_on_message.assert_awaited_once_with(
            "msg-001",
            "completed",
            message_text="Done!",
            artifacts=[
                {
                    "artifactId": "msg-001-response",
                    "name": "response",
                    "parts": [{"kind": "text", "text": "Done!"}],
                }
            ],
        )
        expected_artifacts = [
            {
                "artifactId": "msg-001-response",
                "name": "response",
                "parts": [{"kind": "text", "text": "Done!"}],
            }
        ]
        slot_lifecycle.terminate_slot.assert_awaited_once_with(
            room_id="room-001",
            turn_id="turn-001",
            slot_id="msg-001",
            status="completed",
            content="Done!",
            artifacts=expected_artifacts,
            error=None,
            has_partial_content=None,
        )
        h._rmc.resume_queue_from_continuation.assert_awaited_once_with(
            message_id="msg-001",
            task_result_text="Done!",
            failed=False,
        )

    @pytest.mark.asyncio
    async def test_uses_resolved_terminal_text_from_database_layer(self):
        db = MagicMock()
        db.update_task_state_on_message = AsyncMock(
            return_value=(True, "resolved from artifacts")
        )
        db.accumulate_artifact_on_message = AsyncMock(return_value=True)
        db.get_pending_continuation_on_message = AsyncMock(return_value=None)
        h = _make_handler(db=db)
        event = AgentEvent(kind="response", **_base_event(), text=None)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                AsyncMock(return_value=True),
            )
            await h.handle(event)

        h._task_writer.update_task_state_on_message.assert_awaited_once_with(
            "msg-001",
            "completed",
            message_text=None,
            artifacts=None,
        )
        h._rmc.resume_queue_from_continuation.assert_awaited_once_with(
            message_id="msg-001",
            task_result_text="resolved from artifacts",
            failed=False,
        )

    @pytest.mark.asyncio
    async def test_skip_persist_response(self):
        h = _make_handler()
        event = AgentEvent(
            kind="response",
            **_base_event(),
            text="Done!",
            skip_persist=True,
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                AsyncMock(return_value=True),
            )
            await h.handle(event)

        h._message_writer.update_task_state_on_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sends_agent_response_for_parts(self):
        h = _make_handler()
        event = AgentEvent(
            kind="response",
            **_base_event(),
            text="Done!",
            parts=[{"kind": "file"}],
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                AsyncMock(return_value=True),
            )
            await h.handle(event)

        # send_agent_response removed — _notify() delivers parts via task_update
        h._delivery.send_agent_response.assert_not_awaited()


class TestOrchestrationResultIngestorHook:
    @pytest.mark.asyncio
    async def test_default_hook_is_no_op_for_terminal_response(self):
        h = _make_handler()
        event = AgentEvent(kind="response", **_base_event(), text="Done!")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                AsyncMock(return_value=True),
            )
            await h.handle(event)

        h._rmc.resume_queue_from_continuation.assert_awaited_once_with(
            message_id="msg-001",
            task_result_text="Done!",
            failed=False,
        )

    @pytest.mark.asyncio
    async def test_hook_runs_after_terminal_persist_and_notify_before_resume(self):
        events: list[str] = []

        async def persist_completed(*args, **kwargs):
            events.append("persist")
            return True, "resolved text"

        async def notify(*args, **kwargs):
            events.append("notify")
            return True

        async def resume(*args, **kwargs):
            events.append("resume")
            return True

        async def ingest(result):
            events.append("hook")
            assert isinstance(result, AgentResultRead)

        db = MagicMock()
        db.update_task_state_on_message = AsyncMock(side_effect=persist_completed)
        db.accumulate_artifact_on_message = AsyncMock(return_value=True)
        db.get_pending_continuation_on_message = AsyncMock(return_value=None)
        rmc = MagicMock()
        rmc.resume_queue_from_continuation = AsyncMock(side_effect=resume)
        service = MagicMock()
        service.ingest_agent_result = AsyncMock(side_effect=ingest)
        bind_orchestration_result_ingestor(service)
        h = _make_handler(db=db, rmc=rmc)
        artifacts = [{"artifactId": "artifact-1", "parts": [{"kind": "text"}]}]
        event = AgentEvent(
            kind="response",
            **_base_event(),
            text="raw text",
            artifacts=artifacts,
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                AsyncMock(side_effect=notify),
            )
            await h.handle(event)

        assert events == ["persist", "notify", "hook", "resume"]
        service.ingest_agent_result.assert_awaited_once()
        result = service.ingest_agent_result.await_args.args[0]
        assert result.agent_message_id == "msg-001"
        assert result.status == "completed"
        assert result.text == "resolved text"
        assert result.artifacts[0]["artifactId"] == "artifact-1"
        assert result.error is None

    @pytest.mark.asyncio
    async def test_hook_exception_does_not_prevent_legacy_resume_path(self):
        h = _make_handler()
        service = MagicMock()
        service.ingest_agent_result = AsyncMock(side_effect=RuntimeError("boom"))
        bind_orchestration_result_ingestor(service)
        event = AgentEvent(kind="response", **_base_event(), text="Done!")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                AsyncMock(return_value=True),
            )
            await h.handle(event)

        service.ingest_agent_result.assert_awaited_once()
        h._rmc.resume_queue_from_continuation.assert_awaited_once_with(
            message_id="msg-001",
            task_result_text="Done!",
            failed=False,
        )

    @pytest.mark.asyncio
    async def test_parts_only_response_ingestion_is_idempotent(self):
        state = OrchestrationRunState(
            run_id="run-001",
            room_id="room-001",
            user_message_id="user-msg-001",
            goal="collect artifact",
            candidate_agent_ids=["agent-001"],
        )
        ingestor = AgentResultIngestor()

        async def ingest(result):
            nonlocal state
            state = ingestor.ingest(state, result)

        service = MagicMock()
        service.ingest_agent_result = AsyncMock(side_effect=ingest)
        bind_orchestration_result_ingestor(service)
        h = _make_handler()
        event = AgentEvent(
            kind="response",
            **_base_event(),
            text="Done!",
            parts=[{"kind": "data", "data": {"value": 1}}],
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                AsyncMock(return_value=True),
            )
            await h.handle(event)
            await h.handle(event)

        assert service.ingest_agent_result.await_count == 2
        assert len(state.artifacts) == 1
        assert state.agent_outputs[0].artifact_keys == [
            state.artifacts[0]["artifact_key"]
        ]

    @pytest.mark.asyncio
    async def test_hook_maps_error_and_canceled_terminal_events(self):
        h = _make_handler()
        service = MagicMock()
        service.ingest_agent_result = AsyncMock(return_value=None)
        bind_orchestration_result_ingestor(service)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                AsyncMock(return_value=True),
            )
            await h.handle(
                AgentEvent(
                    kind="error",
                    **_base_event(message_id="msg-error"),
                    text="partial",
                    error_text="boom",
                    state="failed",
                )
            )
            await h.handle(
                AgentEvent(
                    kind="canceled",
                    **_base_event(message_id="msg-canceled"),
                    text="stopped",
                )
            )

        error_result = service.ingest_agent_result.await_args_list[0].args[0]
        canceled_result = service.ingest_agent_result.await_args_list[1].args[0]
        assert error_result == AgentResultRead(
            agent_message_id="msg-error",
            agent_id="agent-001",
            status="failed",
            text=None,
            artifacts=[],
            error="Task failed",
        )
        assert canceled_result == AgentResultRead(
            agent_message_id="msg-canceled",
            agent_id="agent-001",
            status="canceled",
            text=None,
            artifacts=[],
            error="Task was canceled",
        )

    @pytest.mark.asyncio
    async def test_failure_events_project_generic_error_before_persist_delivery_and_ingest(
        self,
    ):
        private_text = "PRIVATE_SENTINEL_remote_failure_text"
        private_metadata = "PRIVATE_SENTINEL_remote_failure_metadata"
        h = _make_handler(slot_lifecycle=MagicMock(terminate_slot=AsyncMock()))
        service = MagicMock()
        service.ingest_agent_result = AsyncMock(return_value=None)
        bind_orchestration_result_ingestor(service)
        event = AgentEvent(
            kind="error",
            **_base_event(turn_id="turn-001"),
            text=private_text,
            error_text=private_text,
            state="failed",
            artifacts=[
                {
                    "artifactId": "raw-failure",
                    "metadata": {"private": private_metadata},
                    "parts": [{"kind": "text", "text": private_text}],
                }
            ],
        )

        with pytest.MonkeyPatch.context() as mp:
            notify = AsyncMock(return_value=True)
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                notify,
            )
            await h.handle(event)

        persisted_kwargs = h._task_writer.update_task_state_on_message.await_args.kwargs
        assert persisted_kwargs["message_text"] == "Task failed"
        assert notify.await_args.kwargs["error"] == "Task failed"
        slot_kwargs = h._slot_lifecycle.terminate_slot.await_args.kwargs
        assert slot_kwargs["content"] is None
        assert slot_kwargs["error"] == "Task failed"
        result = service.ingest_agent_result.await_args.args[0]
        assert result.text is None
        assert result.artifacts == []
        assert result.error == "Task failed"
        combined = json.dumps(
            [
                h._task_writer.update_task_state_on_message.await_args.kwargs,
                notify.await_args.kwargs,
                slot_kwargs,
                result.model_dump(),
            ],
            sort_keys=True,
        )
        assert private_text not in combined
        assert private_metadata not in combined

    @pytest.mark.asyncio
    async def test_error_hook_preserves_terminal_state(self):
        h = _make_handler()
        service = MagicMock()
        service.ingest_agent_result = AsyncMock(return_value=None)
        bind_orchestration_result_ingestor(service)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                AsyncMock(return_value=True),
            )
            await h.handle(
                AgentEvent(
                    kind="error",
                    **_base_event(),
                    error_text="nope",
                    state="rejected",
                )
            )

        result = service.ingest_agent_result.await_args.args[0]
        assert result.status == "rejected"
        assert result.error == "Task was rejected by the agent"


# =============================================================================
# Error events (terminal)
# =============================================================================


class TestErrorEvent:
    @pytest.mark.asyncio
    async def test_persists_error_state(self):
        h = _make_handler()
        event = AgentEvent(
            kind="error",
            **_base_event(),
            error_text="boom",
            state="failed",
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                AsyncMock(return_value=True),
            )
            await h.handle(event)

        h._message_writer.update_task_state_on_message.assert_awaited_once_with(
            "msg-001",
            "failed",
            message_text="Task failed",
        )
        h._rmc.resume_queue_from_continuation.assert_awaited_once_with(
            message_id="msg-001",
            task_result_text=None,
            failed=True,
        )

    @pytest.mark.asyncio
    async def test_terminal_notification_failure_does_not_block_error_cleanup(self):
        slot_lifecycle = MagicMock()
        slot_lifecycle.terminate_slot = AsyncMock()
        h = _make_handler(slot_lifecycle=slot_lifecycle)
        event = AgentEvent(
            kind="error",
            **_base_event(),
            error_text="boom",
            text="partial",
            state="failed",
            turn_id="turn-001",
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                AsyncMock(side_effect=RuntimeError("notification store missing read")),
            )
            await h.handle(event)

        h._message_writer.update_task_state_on_message.assert_awaited_once_with(
            "msg-001",
            "failed",
            message_text="Task failed",
        )
        slot_lifecycle.terminate_slot.assert_awaited_once_with(
            room_id="room-001",
            turn_id="turn-001",
            slot_id="msg-001",
            status="failed",
            content=None,
            artifacts=None,
            error="Task failed",
            has_partial_content=None,
        )
        h._rmc.resume_queue_from_continuation.assert_awaited_once_with(
            message_id="msg-001",
            task_result_text=None,
            failed=True,
        )

    @pytest.mark.asyncio
    async def test_preserves_rejected_state(self):
        h = _make_handler()
        event = AgentEvent(
            kind="error",
            **_base_event(),
            error_text="nope",
            state="rejected",
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                AsyncMock(return_value=True),
            )
            await h.handle(event)

        h._message_writer.update_task_state_on_message.assert_awaited_once_with(
            "msg-001",
            "rejected",
            message_text="Task was rejected by the agent",
        )


# =============================================================================
# Canceled events (terminal)
# =============================================================================


class TestCanceledEvent:
    @pytest.mark.asyncio
    async def test_persists_canceled(self):
        h = _make_handler()
        event = AgentEvent(kind="canceled", **_base_event(), text="stopped")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                AsyncMock(return_value=True),
            )
            await h.handle(event)

        h._message_writer.update_task_state_on_message.assert_awaited_once_with(
            "msg-001",
            "canceled",
            message_text="Task was canceled",
        )
        h._rmc.resume_queue_from_continuation.assert_awaited_once_with(
            message_id="msg-001",
            task_result_text=None,
            failed=True,
        )

    @pytest.mark.asyncio
    async def test_terminal_notification_failure_does_not_block_cancel_cleanup(self):
        slot_lifecycle = MagicMock()
        slot_lifecycle.terminate_slot = AsyncMock()
        h = _make_handler(slot_lifecycle=slot_lifecycle)
        event = AgentEvent(
            kind="canceled",
            **_base_event(),
            text="stopped",
            turn_id="turn-001",
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                AsyncMock(side_effect=RuntimeError("notification store missing read")),
            )
            await h.handle(event)

        h._message_writer.update_task_state_on_message.assert_awaited_once_with(
            "msg-001",
            "canceled",
            message_text="Task was canceled",
        )
        slot_lifecycle.terminate_slot.assert_awaited_once_with(
            room_id="room-001",
            turn_id="turn-001",
            slot_id="msg-001",
            status="canceled",
            content=None,
            artifacts=None,
            error=None,
            has_partial_content=None,
        )
        h._rmc.resume_queue_from_continuation.assert_awaited_once_with(
            message_id="msg-001",
            task_result_text=None,
            failed=True,
        )


# =============================================================================
# Interactive events
# =============================================================================


class TestInteractiveEvent:
    @pytest.mark.asyncio
    async def test_persists_interactive(self):
        h = _make_handler()
        event = AgentEvent(
            kind="interactive",
            **_base_event(),
            text="need input",
            state="input-required",
            task_id="t-1",
            context_id="c-1",
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                AsyncMock(return_value=True),
            )
            await h.handle(event)

        h._message_writer.update_task_state_on_message.assert_awaited_once_with(
            "msg-001",
            "input-required",
            message_text=None,
            task_id="t-1",
            context_id="c-1",
        )
        h._rmc.resume_queue_from_continuation.assert_awaited_once_with(
            message_id="msg-001",
            task_result_text="",
            failed=False,
        )

    @pytest.mark.asyncio
    async def test_async_interactive_prompt_only_reaches_hitl_not_persistence_or_notify(
        self,
    ):
        private_prompt = "PRIVATE_SENTINEL_async_interactive_prompt"
        generic_prompt = "The agent needs additional information."
        mock_impl = AsyncMock(return_value=True)
        db = MagicMock()
        db.update_task_state_on_message = AsyncMock(return_value=(True, None))
        db.accumulate_artifact_on_message = AsyncMock(return_value=True)
        db.get_pending_continuation_on_message = AsyncMock(
            return_value={"user_message_id": "user-msg-001"}
        )
        db.get_room_agent_message_by_message_id = AsyncMock(
            return_value=SimpleNamespace(message_id="display-msg-001")
        )
        db.get_room_by_room_id = AsyncMock(return_value=None)
        hitl = SimpleNamespace(
            request_input=AsyncMock(return_value=SimpleNamespace(request_id="hitl-001"))
        )
        h = _make_handler(
            db=db,
            hitl_coordinator=hitl,
            task_notification_impl=mock_impl,
            task_notification_store=db,
        )
        emitter = AsyncMock()
        h.bind_execution_event_deps(emitter)
        event = AgentEvent(
            kind="interactive",
            **_base_event(),
            text=private_prompt,
            state="input-required",
            task_id="t-1",
            context_id="c-1",
            details=private_prompt,
        )

        await h.handle(event)

        hitl.request_input.assert_awaited_once()
        assert hitl.request_input.await_args.kwargs["prompt"] == generic_prompt
        assert private_prompt not in repr(hitl.request_input.await_args.kwargs)
        persisted_kwargs = db.update_task_state_on_message.await_args.kwargs
        assert persisted_kwargs["message_text"] is None
        notify_payload = mock_impl.await_args.kwargs
        assert private_prompt not in repr(notify_payload)
        emitter_payload = emitter.await_args.kwargs
        assert private_prompt not in json.dumps(emitter_payload, sort_keys=True)

    @pytest.mark.asyncio
    async def test_creates_hitl_request_for_async_interactive_continuation(self):
        call_order = []
        mock_impl = AsyncMock(return_value=True)
        db = MagicMock()
        db.update_task_state_on_message = AsyncMock(return_value=(True, None))
        db.accumulate_artifact_on_message = AsyncMock(return_value=True)
        db.get_pending_continuation_on_message = AsyncMock(
            return_value={"user_message_id": "user-msg-001"}
        )
        db.get_pending_hitl_requests_for_message = AsyncMock(return_value=[])
        db.get_room_agent_message_by_message_id = AsyncMock(
            return_value=SimpleNamespace(message_id="display-msg-001")
        )
        db.get_room_by_room_id = AsyncMock(
            return_value=SimpleNamespace(room_agent_set={"agent-001": "Agent X"})
        )
        hitl = SimpleNamespace(
            request_input=AsyncMock(
                side_effect=lambda **_kwargs: (
                    call_order.append("hitl") or SimpleNamespace(request_id="hitl-001")
                )
            )
        )
        mock_impl.side_effect = lambda *_args, **_kwargs: (
            call_order.append("task_update") or True
        )
        h = _make_handler(
            db=db,
            hitl_coordinator=hitl,
            task_notification_impl=mock_impl,
            task_notification_store=db,
        )
        event = AgentEvent(
            kind="interactive",
            **_base_event(),
            text="need input",
            state="input-required",
            task_id="t-1",
            context_id="c-1",
        )
        emitter = AsyncMock()
        h.bind_execution_event_deps(emitter)

        await h.handle(event)

        assert call_order == ["hitl", "task_update"]
        mock_impl.assert_awaited_once()
        task_update_call = mock_impl.call_args.kwargs
        assert task_update_call["message_id"] == "msg-001"
        assert task_update_call["emit_processing_status"] is False

        hitl.request_input.assert_awaited_once_with(
            room_id="room-001",
            user_message_id="user-msg-001",
            source="agent",
            prompt="The agent needs additional information.",
            agent_id="agent-001",
            agent_name="Agent X",
            a2a_task_id="t-1",
            a2a_context_id="c-1",
            continuation_message_id="msg-001",
            display_message_id="display-msg-001",
        )
        emitter.assert_awaited_once_with(
            room_id="room-001",
            status="awaiting_input",
            message_id="user-msg-001",
            lifecycle_message_id="user-msg-001",
            record_lifecycle=True,
            client_request_id=None,
            details=None,
            error_message=None,
        )
        h._delivery.send_processing_status.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_hitl_request_for_async_auth_required_continuation(self):
        mock_impl = AsyncMock(return_value=True)
        db = MagicMock()
        db.update_task_state_on_message = AsyncMock(return_value=(True, None))
        db.accumulate_artifact_on_message = AsyncMock(return_value=True)
        db.get_pending_continuation_on_message = AsyncMock(
            return_value={"user_message_id": "user-msg-001"}
        )
        db.get_pending_hitl_requests_for_message = AsyncMock(return_value=[])
        db.get_room_agent_message_by_message_id = AsyncMock(
            return_value=SimpleNamespace(message_id="display-msg-001")
        )
        db.get_room_by_room_id = AsyncMock(
            return_value=SimpleNamespace(room_agent_set={"agent-001": "Agent X"})
        )
        hitl = SimpleNamespace(
            request_input=AsyncMock(return_value=SimpleNamespace(request_id="hitl-001"))
        )
        h = _make_handler(
            db=db,
            hitl_coordinator=hitl,
            task_notification_impl=mock_impl,
            task_notification_store=db,
        )
        event = AgentEvent(
            kind="interactive",
            **_base_event(),
            text="Please authenticate.",
            state="auth-required",
            task_id="t-1",
            context_id="c-1",
        )
        emitter = AsyncMock()
        h.bind_execution_event_deps(emitter)

        await h.handle(event)

        mock_impl.assert_awaited_once()
        task_update_call = mock_impl.call_args.kwargs
        assert task_update_call["emit_processing_status"] is False
        hitl.request_input.assert_awaited_once_with(
            room_id="room-001",
            user_message_id="user-msg-001",
            source="agent",
            prompt="The agent needs additional information.",
            agent_id="agent-001",
            agent_name="Agent X",
            a2a_task_id="t-1",
            a2a_context_id="c-1",
            continuation_message_id="msg-001",
            display_message_id="display-msg-001",
        )
        emitter.assert_awaited_once_with(
            room_id="room-001",
            status="awaiting_input",
            message_id="user-msg-001",
            lifecycle_message_id="user-msg-001",
            record_lifecycle=True,
            client_request_id=None,
            details=None,
            error_message=None,
        )

    @pytest.mark.asyncio
    async def test_reuses_existing_async_pending_hitl_request_for_reprojection_and_sse(
        self,
    ):
        db = MagicMock()
        db.update_task_state_on_message = AsyncMock(return_value=(True, None))
        db.accumulate_artifact_on_message = AsyncMock(return_value=True)
        db.get_pending_continuation_on_message = AsyncMock(
            return_value={"user_message_id": "user-msg-001"}
        )
        db.get_pending_hitl_requests_for_message = AsyncMock(
            return_value=[
                {
                    "request_id": "hitl-existing",
                    "status": "pending",
                    "continuation_message_id": "msg-001",
                }
            ]
        )
        db.get_room_agent_message_by_message_id = AsyncMock(
            return_value=SimpleNamespace(message_id="display-msg-001")
        )
        hitl = SimpleNamespace(
            request_input=AsyncMock(
                return_value=SimpleNamespace(request_id="hitl-existing")
            )
        )
        h = _make_handler(db=db, hitl_coordinator=hitl)
        emitter = AsyncMock()
        h.bind_execution_event_deps(emitter)
        event = AgentEvent(
            kind="interactive",
            **_base_event(),
            text="need input",
            state="input-required",
            task_id="t-1",
            context_id="c-1",
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                AsyncMock(return_value=True),
            )
            await h.handle(event)

        hitl.request_input.assert_awaited_once_with(
            room_id="room-001",
            user_message_id="user-msg-001",
            source="agent",
            prompt="The agent needs additional information.",
            agent_id="agent-001",
            agent_name=None,
            a2a_task_id="t-1",
            a2a_context_id="c-1",
            continuation_message_id="msg-001",
            display_message_id="display-msg-001",
        )
        emitter.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_logs_agent_name_lookup_failure_without_blocking_hitl(self):
        db = MagicMock()
        db.update_task_state_on_message = AsyncMock(return_value=(True, None))
        db.accumulate_artifact_on_message = AsyncMock(return_value=True)
        db.get_pending_continuation_on_message = AsyncMock(
            return_value={"user_message_id": "user-msg-001"}
        )
        db.get_pending_hitl_requests_for_message = AsyncMock(return_value=[])
        db.get_room_agent_message_by_message_id = AsyncMock(
            return_value=SimpleNamespace(message_id="display-msg-001")
        )
        db.get_room_by_room_id = AsyncMock(side_effect=RuntimeError("db down"))
        hitl = SimpleNamespace(
            request_input=AsyncMock(return_value=SimpleNamespace(request_id="hitl-001"))
        )
        h = _make_handler(db=db, hitl_coordinator=hitl)
        emitter = AsyncMock()
        h.bind_execution_event_deps(emitter)
        event = AgentEvent(
            kind="interactive",
            **_base_event(),
            text="need input",
            state="input-required",
            task_id="t-1",
            context_id="c-1",
        )
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                AsyncMock(return_value=True),
            )
            debug = MagicMock()
            mp.setattr("execution.dispatch.response_handler.logger.debug", debug)
            await h.handle(event)

        debug.assert_called_once_with("agent name lookup failed", exc_info=True)
        hitl.request_input.assert_awaited_once()
        assert hitl.request_input.call_args.kwargs["agent_name"] is None
        emitter.assert_awaited_once()
        h._delivery.send_processing_status.assert_not_awaited()


# =============================================================================
# Non-terminal events
# =============================================================================


class TestSubmittedEvent:
    @pytest.mark.asyncio
    async def test_sends_sse_submitted(self):
        h = _make_handler()
        event = AgentEvent(
            kind="task_submitted",
            **_base_event(),
            task_id="t-1",
            agent_name="Agent X",
        )
        await h.handle(event)
        h._delivery.send_task_submitted.assert_awaited_once()
        h._message_writer.update_task_state_on_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sends_sse_submitted_with_resolved_client_request_id(self):
        db = MagicMock()
        db.resolve_client_request_id_for_message_id = AsyncMock(return_value="cr-001")
        h = _make_handler(db=db)
        event = AgentEvent(
            kind="task_submitted",
            **_base_event(),
            task_id="t-1",
            agent_name="Agent X",
        )
        await h.handle(event)

        call_kwargs = h._delivery.send_task_submitted.call_args.kwargs
        assert call_kwargs["client_request_id"] == "cr-001"
        db.resolve_client_request_id_for_message_id.assert_awaited_once_with("msg-001")

    @pytest.mark.asyncio
    async def test_sends_sse_submitted_with_public_task_label(self):
        private_task = "PRIVATE_SENTINEL_generic_submitted_task_content"
        private_message = "PRIVATE_SENTINEL_generic_submitted_message_text"
        public_label = "Requesting public broker analysis"
        db = MagicMock()
        db.resolve_client_request_id_for_message_id = AsyncMock(return_value=None)
        db.get_room_agent_message_by_message_id = AsyncMock(
            return_value=SimpleNamespace(
                extend_info={"public_task_label": public_label},
                task_content=private_task,
                message_content=SimpleNamespace(message_text=private_message),
            )
        )
        h = _make_handler(db=db)
        event = AgentEvent(
            kind="task_submitted",
            **_base_event(),
            task_id="t-1",
            agent_name="Agent X",
        )

        await h.handle(event)

        call_kwargs = h._delivery.send_task_submitted.call_args.kwargs
        assert call_kwargs["task_content"] == public_label
        delivered_payload = json.dumps(call_kwargs, default=str)
        assert private_task not in delivered_payload
        assert private_message not in delivered_payload


class TestStatusUpdateEvent:
    @pytest.mark.asyncio
    async def test_drops_status_update_text(self):
        h = _make_handler()
        event = AgentEvent(
            kind="status_update",
            **_base_event(),
            text="still working",
        )
        await h.handle(event)
        h._delivery.send_task_update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_drops_raw_remote_status_text(self):
        h = _make_handler()
        event = AgentEvent(
            kind="status_update",
            **_base_event(),
            text="PRIVATE_SENTINEL_remote_working_status",
            state="working",
        )
        await h.handle(event)
        h._delivery.send_task_update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_sse_for_empty_text(self):
        h = _make_handler()
        event = AgentEvent(
            kind="status_update",
            **_base_event(),
            text="",
        )
        await h.handle(event)
        h._delivery.send_task_update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_status_update_does_not_resolve_client_request_id_when_dropped(self):
        db = MagicMock()
        db.resolve_client_request_id_for_message_id = AsyncMock(return_value="cr-002")
        h = _make_handler(db=db)
        event = AgentEvent(
            kind="status_update",
            **_base_event(),
            text="still working",
        )
        await h.handle(event)

        h._delivery.send_task_update.assert_not_awaited()
        db.resolve_client_request_id_for_message_id.assert_not_awaited()


class TestProcessingStatusEvent:
    @pytest.mark.asyncio
    async def test_sends_processing_status_with_lifecycle_id(self):
        h = _make_handler()
        emitter = AsyncMock()
        h.bind_execution_event_deps(emitter)
        event = AgentEvent(
            kind="processing_status",
            **_base_event(),
            lifecycle_message_id="umsg-001",
            state="completed",
            details="all done",
        )
        await h.handle(event)
        emitter.assert_awaited_once_with(
            room_id="room-001",
            status="completed",
            message_id="msg-001",
            lifecycle_message_id="umsg-001",
            record_lifecycle=True,
            client_request_id=None,
            details=None,
            error_message=None,
        )
        h._delivery.send_processing_status.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_records_when_lifecycle_message_id_is_explicit(self):
        h = _make_handler()
        emitter = AsyncMock()
        h.bind_execution_event_deps(emitter)
        event = AgentEvent(
            kind="processing_status",
            **_base_event(message_id="display-msg-001"),
            lifecycle_message_id="umsg-001",
            state="completed",
            details="all done",
            client_request_id="cr-1",
        )

        await h.handle(event)

        emitter.assert_awaited_once_with(
            room_id="room-001",
            status="completed",
            message_id="display-msg-001",
            lifecycle_message_id="umsg-001",
            record_lifecycle=True,
            client_request_id="cr-1",
            details=None,
            error_message=None,
        )
        h._delivery.send_processing_status.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_processing_status_without_lifecycle_id_is_dropped(self):
        h = _make_handler()
        emitter = AsyncMock()
        h.bind_execution_event_deps(emitter)
        event = AgentEvent(
            kind="processing_status",
            **_base_event(),
            state="completed",
            details="all done",
        )

        await h.handle(event)

        emitter.assert_not_awaited()
        h._delivery.send_processing_status.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_processing_status_resolves_client_request_id_for_emitter(self):
        db = MagicMock()
        db.resolve_client_request_id_for_message_id = AsyncMock(
            return_value="cr-processor"
        )
        h = _make_handler(db=db)
        emitter = AsyncMock()
        h.bind_execution_event_deps(emitter)
        event = AgentEvent(
            kind="processing_status",
            **_base_event(),
            lifecycle_message_id="umsg-001",
            state="completed",
            details="all done",
        )

        await h.handle(event)

        emitter.assert_awaited_once_with(
            room_id="room-001",
            status="completed",
            message_id="msg-001",
            lifecycle_message_id="umsg-001",
            record_lifecycle=True,
            client_request_id="cr-processor",
            details=None,
            error_message=None,
        )
        db.resolve_client_request_id_for_message_id.assert_awaited_once_with("msg-001")


# =============================================================================
# Orchestration resume error handling
# =============================================================================


class TestResumeOrchestrationErrorHandling:
    @pytest.mark.asyncio
    async def test_resume_exception_does_not_propagate(self):
        rmc = MagicMock()
        rmc.resume_queue_from_continuation = AsyncMock(side_effect=RuntimeError("boom"))
        h = _make_handler(rmc=rmc)
        event = AgentEvent(
            kind="response",
            **_base_event(),
            text="Done!",
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                AsyncMock(return_value=True),
            )
            # Should not raise despite resume failure
            await h.handle(event)


# =============================================================================
# Artifact text-only fallback (no artifact object, only text)
# =============================================================================


class TestArtifactTextFallback:
    """Text-only artifact_update is nonterminal and stays private."""

    @pytest.mark.asyncio
    async def test_text_only_artifact_update_is_dropped(self):
        h = _make_handler()
        event = AgentEvent(
            kind="artifact_update",
            **_base_event(),
            text="chunk",
            artifacts=None,
            append=True,
            last_chunk=False,
        )
        await h.handle(event)
        h._message_writer.accumulate_artifact_on_message.assert_not_awaited()
        h._delivery.send_artifact_update.assert_not_awaited()
        assert event.text == "chunk"
        assert event.append is True
        assert event.last_chunk is False


class TestStatusUpdateSendsTaskUpdate:
    """_on_status drops raw remote status text."""

    @pytest.mark.asyncio
    async def test_status_text_is_dropped(self):
        h = _make_handler()
        event = AgentEvent(
            kind="status_update",
            **_base_event(),
            text="Searching the web...",
        )
        await h.handle(event)
        h._delivery.send_task_update.assert_not_awaited()


# =============================================================================
# Handler-owned notify_task_update method
# =============================================================================


class TestHandlerNotifyTaskUpdate:
    """notify_task_update method delegates to _notify_task_update_impl."""

    def test_response_task_writer_remains_write_only(self):
        assert "get_room_agent_message_by_message_id" not in ResponseTaskWriter.__dict__
        assert "get_room_by_room_id" not in ResponseTaskWriter.__dict__
        assert (
            "resolve_client_request_id_for_agent_message"
            not in ResponseTaskWriter.__dict__
        )

    def test_notification_impl_requires_notification_store_at_construction(self):
        with pytest.raises(RuntimeError, match="Task notification store"):
            _make_handler(task_notification_impl=AsyncMock(return_value=True))

    @pytest.mark.asyncio
    async def test_delegates_to_shared_impl(self):
        mock_impl = AsyncMock(return_value=True)
        notification_store = MagicMock()
        h = _make_handler(
            task_notification_impl=mock_impl,
            task_notification_store=notification_store,
        )
        emitter = AsyncMock()
        h.bind_execution_event_deps(emitter)

        result = await h.notify_task_update(
            message_id="msg-001",
            state=MagicMock(value="completed"),
            room_id="room-001",
            user_id="user-001",
            error=None,
            parts=None,
        )

        assert result is True
        mock_impl.assert_awaited_once()
        call_args = mock_impl.call_args
        # First positional arg is the handler's read-capable notification store.
        assert call_args[0][0] is notification_store
        assert call_args[0][0] is not h._task_writer
        # Third positional arg is the handler's sse instance
        assert call_args[0][2] is h._delivery
        assert call_args.kwargs["emit_processing_status"] is True
        assert call_args.kwargs["processing_status_emitter"] is emitter

    @pytest.mark.asyncio
    async def test_processing_status_terminal_close_out_does_not_duplicate_emit(self):
        mock_impl = AsyncMock(return_value=True)
        h = _make_handler(
            task_notification_impl=mock_impl,
            task_notification_store=MagicMock(),
        )
        emitter = AsyncMock()
        h.bind_execution_event_deps(emitter)
        event = AgentEvent(
            kind="processing_status",
            **_base_event(),
            lifecycle_message_id="umsg-001",
            state="completed",
        )

        await h.handle(event)

        call_args = mock_impl.call_args
        assert call_args.kwargs["emit_processing_status"] is False
        emitter.assert_awaited_once_with(
            room_id="room-001",
            status="completed",
            message_id="msg-001",
            lifecycle_message_id="umsg-001",
            record_lifecycle=True,
            client_request_id=None,
            details=None,
            error_message=None,
        )

    @pytest.mark.asyncio
    async def test_processing_status_terminal_completed_projects_public_output(self):
        private_text = "PRIVATE_SENTINEL_hub_terminal_text"
        private_bytes = "PRIVATE_SENTINEL_hub_terminal_bytes"
        private_metadata = "PRIVATE_SENTINEL_hub_terminal_metadata"
        mock_impl = AsyncMock(return_value=True)
        slot_lifecycle = MagicMock(terminate_slot=AsyncMock())
        h = _make_handler(
            slot_lifecycle=slot_lifecycle,
            task_notification_impl=mock_impl,
            task_notification_store=MagicMock(),
        )
        emitter = AsyncMock()
        h.bind_execution_event_deps(emitter)
        service = MagicMock()
        service.ingest_agent_result = AsyncMock(return_value=None)
        bind_orchestration_result_ingestor(service)
        event = AgentEvent(
            kind="processing_status",
            **_base_event(turn_id="turn-001"),
            lifecycle_message_id="umsg-001",
            state="completed",
            text=private_text,
            details=private_text,
            artifacts=[
                {
                    "artifactId": "hub-artifact",
                    "name": "response",
                    "metadata": {"private": private_metadata},
                    "parts": [
                        {
                            "kind": "text",
                            "text": "Visible hub output",
                            "metadata": {"private": private_metadata},
                        },
                        {
                            "kind": "file",
                            "file": {
                                "bytes": private_bytes,
                                "mime_type": "text/plain",
                                "name": "private.txt",
                            },
                        },
                    ],
                }
            ],
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "common.utils.a2a_helpers.materialize_inline_file_parts",
                AsyncMock(side_effect=RuntimeError("S3 unavailable")),
            )
            await h.handle(event)

        persisted_kwargs = h._task_writer.update_task_state_on_message.await_args.kwargs
        assert persisted_kwargs["message_text"] == "Visible hub output"
        result = service.ingest_agent_result.await_args.args[0]
        payloads = [
            persisted_kwargs["artifacts"],
            slot_lifecycle.terminate_slot.await_args.kwargs["artifacts"],
            result.artifacts,
            emitter.await_args.kwargs["details"],
            mock_impl.await_args.kwargs,
            event.model_dump() if hasattr(event, "model_dump") else event.__dict__,
        ]
        combined = json.dumps(payloads, sort_keys=True, default=str)
        assert "Visible hub output" in combined
        assert private_text not in combined
        assert private_bytes not in combined
        assert private_metadata not in combined
        assert result.text == "Visible hub output"
        assert result.error is None
        slot_lifecycle.terminate_slot.assert_awaited_once()
        emitter.assert_awaited_once_with(
            room_id="room-001",
            status="completed",
            message_id="msg-001",
            lifecycle_message_id="umsg-001",
            record_lifecycle=True,
            client_request_id=None,
            details=None,
            error_message=None,
        )

    @pytest.mark.asyncio
    async def test_processing_status_terminal_close_out_ingests_agent_result(self):
        mock_impl = AsyncMock(return_value=True)
        h = _make_handler(
            task_notification_impl=mock_impl,
            task_notification_store=MagicMock(),
        )
        emitter = AsyncMock()
        h.bind_execution_event_deps(emitter)
        service = MagicMock()
        service.ingest_agent_result = AsyncMock(return_value=None)
        bind_orchestration_result_ingestor(service)
        artifacts = [{"artifactId": "artifact-1", "parts": [{"kind": "text"}]}]
        event = AgentEvent(
            kind="processing_status",
            **_base_event(),
            lifecycle_message_id="umsg-001",
            state="failed",
            details="relay failed",
            artifacts=artifacts,
        )

        await h.handle(event)

        service.ingest_agent_result.assert_awaited_once_with(
            AgentResultRead(
                agent_message_id="msg-001",
                agent_id="agent-001",
                status="failed",
                text=None,
                artifacts=[],
                error="Task failed",
            )
        )
        emitter.assert_awaited_once_with(
            room_id="room-001",
            status="failed",
            message_id="msg-001",
            lifecycle_message_id="umsg-001",
            record_lifecycle=True,
            client_request_id=None,
            details={"message": "Task failed"},
            error_message="Task failed",
        )

    @pytest.mark.asyncio
    async def test_processing_status_terminal_non_task_state_ingests_agent_result(self):
        mock_impl = AsyncMock(return_value=True)
        h = _make_handler(
            task_notification_impl=mock_impl,
            task_notification_store=MagicMock(),
        )
        emitter = AsyncMock()
        h.bind_execution_event_deps(emitter)
        service = MagicMock()
        service.ingest_agent_result = AsyncMock(return_value=None)
        bind_orchestration_result_ingestor(service)
        event = AgentEvent(
            kind="processing_status",
            **_base_event(),
            lifecycle_message_id="umsg-001",
            state="rate_limited",
            details={"message": "too many requests"},
        )

        await h.handle(event)

        mock_impl.assert_awaited_once()
        service.ingest_agent_result.assert_awaited_once_with(
            AgentResultRead(
                agent_message_id="msg-001",
                agent_id="agent-001",
                status="rate_limited",
                text=None,
                artifacts=[],
                error="Task failed",
            )
        )
        emitter.assert_awaited_once_with(
            room_id="room-001",
            status="rate_limited",
            message_id="msg-001",
            lifecycle_message_id="umsg-001",
            record_lifecycle=True,
            client_request_id=None,
            details={"message": "Task failed"},
            error_message=None,
        )

    @pytest.mark.asyncio
    async def test_notify_helper_delegates_to_method(self):
        """_notify helper calls self.notify_task_update with event fields."""
        mock_impl = AsyncMock(return_value=True)
        h = _make_handler(
            task_notification_impl=mock_impl,
            task_notification_store=MagicMock(),
        )
        from a2a.types import TaskState

        event = AgentEvent(
            kind="response",
            **_base_event(),
            text="Done!",
            parts=[{"kind": "text"}],
        )
        await h._notify(event, TaskState.completed)

        mock_impl.assert_awaited_once()
        call_kw = mock_impl.call_args.kwargs
        assert call_kw["message_id"] == "msg-001"
        assert call_kw["room_id"] == "room-001"
        assert call_kw["parts"] == [{"kind": "text"}]
        assert call_kw["emit_processing_status"] is True
