from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from container import create_room_deps


class _FakeMongo:
    def collection(self, name: str):
        return SimpleNamespace(name=name)


@pytest.mark.asyncio
async def test_create_room_deps_wires_attachment_metadata_reader_to_facade():
    reader = SimpleNamespace(
        get_for_room_file=AsyncMock(
            return_value={
                "file_id": "file1",
                "room_id": "room1",
                "file_name": "doc.pdf",
            }
        )
    )

    deps = create_room_deps(
        mongo=_FakeMongo(),
        agent_registry=SimpleNamespace(),
        membership_source=SimpleNamespace(),
        attachment_metadata_reader=reader,
    )

    attachment = await deps.room_registry.get_attachment_for_room_file(
        "room1", "file1"
    )

    assert attachment == {
        "file_id": "file1",
        "room_id": "room1",
        "file_name": "doc.pdf",
    }
    reader.get_for_room_file.assert_awaited_once_with("room1", "file1")


def test_container_startup_binds_file_storage_to_room_runtime():
    with open("container.py") as source_file:
        source = source_file.read()

    assert "attachment_metadata_reader=file_storage" in source
    assert "room_runtime.bind_attachment_metadata_reader(file_storage)" in source
    assert "room_runtime.bind_attachment_content_reader(file_storage)" in source
    assert "room_runtime.bind_a2a_inline_file_limits(" in source
    assert "max_raw_bytes=runtime.settings.a2a_inline_file_max_raw_bytes" in source
    assert (
        "max_encoded_bytes=runtime.settings.a2a_inline_message_max_encoded_bytes"
        in source
    )
