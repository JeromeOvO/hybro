from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest

from execution.adapters.resources import RoomFilesResourceMaterializer
from execution.orchestrator.a2a_runtime.models import (
    FrozenCallResourceManifest,
    FrozenCallResourceRef,
)
from execution.orchestrator.a2a_runtime.resources import ResourceSelectionError


def _future_deadline() -> datetime:
    return datetime.now(UTC) + timedelta(minutes=1)


class FakeRoomFiles:
    def __init__(self, files):
        self.files = files

    async def get_bytes(self, file_id, *, max_bytes):
        return self.files.get(file_id)

    async def get_for_room_file(self, room_id, file_id):
        return None


class FakeWriter:
    def __init__(self):
        self.calls = []

    async def __call__(self, call, artifact_ref, observation_id):
        self.calls.append((call, artifact_ref, observation_id))
        return f"durable:{artifact_ref}"


def _ref(ref_id, kind, content_digest=None, mime_type=None):
    return FrozenCallResourceRef(
        ref_id=ref_id,
        kind=kind,
        room_id="room-1",
        room_epoch=1,
        source_message_id="message-1",
        mime_type=mime_type,
        size_bytes=0,
        content_digest=content_digest or "",
    )


async def test_context_ref_materializes_to_text_part():
    content = b"hello world"
    digest = sha256(content).hexdigest()
    ref = _ref(
        "ctx:message:message-1",
        "context",
        content_digest=digest,
        mime_type="text/plain",
    )
    manifest = FrozenCallResourceManifest(
        manifest_id="m", refs=[ref], content_digest="manifest-digest"
    )

    async def context_text_reader(message_id):
        assert message_id == "message-1"
        return "hello world"

    adapter = RoomFilesResourceMaterializer(
        room_files=FakeRoomFiles({}),
        artifact_writer=FakeWriter(),
        context_text_reader=context_text_reader,
    )
    parts = await adapter.materialize(
        manifest,
        room_id="room-1",
        room_epoch=1,
        allowed_input_modes=["text"],
        deadline_at=_future_deadline(),
    )
    assert len(parts) == 1
    assert parts[0].kind == "text"
    assert parts[0].payload == "hello world"
    assert parts[0].content_digest == digest


async def test_attachment_ref_materializes_to_file_part():
    content = b"file-bytes"
    digest = sha256(content).hexdigest()
    ref = _ref(
        "file-1", "attachment", content_digest=digest, mime_type="application/pdf"
    )
    manifest = FrozenCallResourceManifest(
        manifest_id="m", refs=[ref], content_digest="manifest-digest"
    )
    adapter = RoomFilesResourceMaterializer(
        room_files=FakeRoomFiles({"file-1": content}),
        artifact_writer=FakeWriter(),
    )
    parts = await adapter.materialize(
        manifest,
        room_id="room-1",
        room_epoch=1,
        allowed_input_modes=["application/pdf"],
        deadline_at=_future_deadline(),
    )
    assert parts[0].kind == "file"
    assert parts[0].payload["bytes"] == base64.b64encode(content).decode("ascii")
    assert parts[0].mime_type == "application/pdf"


async def test_changed_content_is_rejected():
    ref = _ref("file-1", "attachment", content_digest="expected-digest")
    manifest = FrozenCallResourceManifest(
        manifest_id="m", refs=[ref], content_digest="manifest-digest"
    )
    adapter = RoomFilesResourceMaterializer(
        room_files=FakeRoomFiles({"file-1": b"actual"}),
        artifact_writer=FakeWriter(),
    )
    with pytest.raises(ResourceSelectionError, match="changed"):
        await adapter.materialize(
            manifest,
            room_id="room-1",
            room_epoch=1,
            allowed_input_modes=["file"],
            deadline_at=_future_deadline(),
        )


async def test_inbound_artifacts_delegate_to_writer():
    writer = FakeWriter()
    adapter = RoomFilesResourceMaterializer(
        room_files=FakeRoomFiles({}),
        artifact_writer=writer,
    )
    refs = await adapter.materialize_inbound_artifacts(
        call=object(),
        artifact_refs=["https://example/file.bin"],
        observation_id="observation-1",
    )
    assert refs == ["durable:https://example/file.bin"]
    assert writer.calls[0][1] == "https://example/file.bin"
