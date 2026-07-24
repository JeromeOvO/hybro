from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api_gateway.routes.files_routes import download_file, upload_file
from common.auth import ClerkUser


class UploadStub:
    filename = "report.txt"
    content_type = "text/plain"

    def __init__(self, content: bytes):
        self.content = content
        self.requested_size = None

    async def read(self, size: int = -1):
        self.requested_size = size
        return self.content[:size]


class OwnershipStub:
    async def get_room_owner(self, room_id):
        return "user-1" if room_id == "room-1" else None


class FilesStub:
    def __init__(self, *, mime_type="text/plain; charset=utf-8"):
        self.uploaded = None
        self.mime_type = mime_type

    async def upload(self, **kwargs):
        self.uploaded = kwargs
        return SimpleNamespace(
            file_id="a" * 32,
            url="/api/v1/files/" + "a" * 32 + "/content",
            mime_type="text/plain",
            file_name="report.txt",
            size_bytes=len(kwargs["file_bytes"]),
        )

    async def get_ready_file(self, file_id, *, owner_id=None):
        if file_id != "a" * 32 or owner_id != "user-1":
            return None
        return SimpleNamespace(
            file_id=file_id,
            file_name='report "final".txt',
            mime_type=self.mime_type,
            size_bytes=5,
        )

    async def stream(self, file_id, chunk_size):
        assert file_id == "a" * 32
        assert chunk_size > 0
        yield b"he"
        yield b"llo"


def _user():
    return ClerkUser(user_id="user-1", session_id="session-1", claims={})


async def test_upload_route_reads_only_one_byte_past_limit():
    upload = UploadStub(b"hello")
    storage = FilesStub()

    response = await upload_file(
        file=upload,
        room_id="room-1",
        user=_user(),
        storage=storage,
        room_ownership=OwnershipStub(),
    )

    assert upload.requested_size == 5 * 1024 * 1024 + 1
    assert response.file_url == "/api/v1/files/" + "a" * 32 + "/content"


async def test_upload_route_rejects_oversize_before_storage():
    upload = UploadStub(b"x" * (5 * 1024 * 1024 + 1))
    storage = FilesStub()

    with pytest.raises(HTTPException) as exc_info:
        await upload_file(
            file=upload,
            room_id="room-1",
            user=_user(),
            storage=storage,
            room_ownership=OwnershipStub(),
        )

    assert exc_info.value.status_code == 413
    assert storage.uploaded is None


async def test_download_route_streams_owned_ready_file_with_safe_headers():
    response = await download_file(
        file_id="a" * 32,
        user=_user(),
        storage=FilesStub(),
    )

    body = b"".join([chunk async for chunk in response.body_iterator])
    assert body == b"hello"
    assert response.headers["content-length"] == "5"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"].startswith(
        "attachment; filename*=UTF-8''"
    )
    assert response.headers["content-type"] == "text/plain; charset=utf-8"


async def test_download_route_replaces_unsafe_stored_mime_type():
    response = await download_file(
        file_id="a" * 32,
        user=_user(),
        storage=FilesStub(mime_type="text/plain\r\nX-Injected: yes"),
    )

    assert response.headers["content-type"] == "application/octet-stream"
    assert b"\r" not in dict(response.raw_headers)[b"content-type"]
    assert b"\n" not in dict(response.raw_headers)[b"content-type"]
