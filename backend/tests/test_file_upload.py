from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from platform_module import PlatformConfig, PlatformDeps
from platform_module.files import PlatformFileStorage

from api.files import upload_file as upload_file_route
from common.dto import FileInfo
from common.errors import FileStoragePlatformError


class FakeFileStorage:
    def __init__(self, result: FileInfo | None = None, error: Exception | None = None):
        self.result = result or FileInfo(
            file_id="file-1",
            file_name="test.png",
            mime_type="image/png",
            size_bytes=8,
            url="https://s3/presigned",
        )
        self.error = error
        self.calls: list[dict] = []

    async def upload(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result

    async def get_url(self, file_id: str, ttl: int = 3600) -> str | None:
        return self.result.url

    async def delete(self, file_id: str) -> bool:
        return True

    async def list_for_room(self, room_id: str) -> list[FileInfo]:
        return [self.result]


class FakeRoomOwnershipReader:
    def __init__(self, owner_id: str | None = "user1") -> None:
        self.owner_id = owner_id
        self.calls: list[str] = []

    async def get_room_owner(self, room_id: str) -> str | None:
        self.calls.append(room_id)
        return self.owner_id

    async def verify_room_agent_membership(self, room_id: str, agent_id: str) -> bool:
        return True

    async def verify_room_hub_ownership(self, room_id: str, hub_id: str) -> bool:
        return True


@pytest.fixture
def upload_file():
    f = MagicMock()
    f.filename = "test.png"
    f.content_type = "image/png"
    f.read = AsyncMock(return_value=b"\x89PNG\r\n\x1a\n")
    return f


@pytest.fixture
def mime_detector():
    return PlatformFileStorage(PlatformConfig(), PlatformDeps())


class TestFileUploadRouteAdapter:
    async def test_upload_route_delegates_to_bound_file_storage(self, upload_file):
        storage = FakeFileStorage()
        room_ownership = FakeRoomOwnershipReader()

        result = await upload_file_route(
            file=upload_file,
            room_id="room1",
            user=SimpleNamespace(user_id="user1"),
            storage=storage,
            room_ownership=room_ownership,
        )

        assert result.file_url == "https://s3/presigned"
        assert result.mime_type == "image/png"
        assert room_ownership.calls == ["room1"]
        assert storage.calls == [
            {
                "file_bytes": b"\x89PNG\r\n\x1a\n",
                "filename": "test.png",
                "owner_id": "user1",
                "room_id": "room1",
                "content_type": "image/png",
            }
        ]

    async def test_upload_route_maps_platform_storage_errors(self, upload_file):
        storage = FakeFileStorage(
            error=FileStoragePlatformError(415, {"error": "unsupported_type"})
        )
        room_ownership = FakeRoomOwnershipReader()

        with pytest.raises(HTTPException) as exc_info:
            await upload_file_route(
                file=upload_file,
                room_id="room1",
                user=SimpleNamespace(user_id="user1"),
                storage=storage,
                room_ownership=room_ownership,
            )

        assert exc_info.value.status_code == 415

    def test_detect_mime_png(self, mime_detector):
        assert mime_detector._detect_mime(b"\x89PNG\r\n\x1a\n") == "image/png"

    def test_detect_mime_jpeg(self, mime_detector):
        assert mime_detector._detect_mime(b"\xff\xd8\xff" + b"x" * 10) == "image/jpeg"

    def test_detect_mime_pdf(self, mime_detector):
        assert mime_detector._detect_mime(b"%PDF-1.4") == "application/pdf"

    def test_detect_mime_unknown(self, mime_detector):
        assert mime_detector._detect_mime(b"random bytes") is None

    def test_mime_compatible_same(self):
        assert PlatformFileStorage._mime_compatible("image/png", "image/png") is True

    def test_mime_compatible_different_major(self):
        assert PlatformFileStorage._mime_compatible("image/png", "application/pdf") is False

    def test_mime_compatible_same_major(self):
        assert PlatformFileStorage._mime_compatible("image/jpeg", "image/png") is True

    def test_mime_compatible_audio_mp4_ftyp(self):
        assert PlatformFileStorage._mime_compatible("audio/mp4", "video/mp4") is True

    def test_mime_compatible_docx_zip(self):
        assert PlatformFileStorage._mime_compatible(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/zip",
        ) is True

    def test_detect_mime_mp3_id3(self, mime_detector):
        assert mime_detector._detect_mime(b"ID3" + b"\x00" * 20) == "audio/mpeg"

    def test_detect_mime_wav(self, mime_detector):
        content = b"RIFF" + b"\x00" * 4 + b"WAVE" + b"\x00" * 20
        assert mime_detector._detect_mime(content) == "audio/wav"

    def test_detect_mime_webm(self, mime_detector):
        assert mime_detector._detect_mime(b"\x1aE\xdf\xa3" + b"\x00" * 20) == "video/webm"

    def test_detect_mime_mp4_ftyp(self, mime_detector):
        content = b"\x00\x00\x00\x18ftyp" + b"\x00" * 20
        assert mime_detector._detect_mime(content) == "video/mp4"

    def test_detect_mime_zip(self, mime_detector):
        assert mime_detector._detect_mime(b"PK\x03\x04" + b"\x00" * 20) == "application/zip"

    def test_mime_compatible_audio_webm_matroska(self):
        assert PlatformFileStorage._mime_compatible("audio/webm", "video/webm") is True

    def test_mime_compatible_xlsx_zip(self):
        assert PlatformFileStorage._mime_compatible(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/zip",
        ) is True
