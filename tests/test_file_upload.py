import io
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException
from services.file_upload_service import FileUploadService

@pytest.fixture
def upload_service():
    svc = FileUploadService()
    svc._s3 = AsyncMock()
    svc._s3.upload_file = AsyncMock(return_value="uploads/r/f/test.png")
    svc._s3.generate_presigned_url = AsyncMock(return_value="https://s3/presigned")
    return svc

def _make_upload_file(content=b"\x89PNG\r\n\x1a\n", filename="test.png", content_type="image/png"):
    f = MagicMock()
    f.filename = filename
    f.content_type = content_type
    f.read = AsyncMock(return_value=content)
    return f

class TestFileUploadService:
    async def test_upload_success(self, upload_service):
        file = _make_upload_file()
        with patch("services.file_upload_service.mongodb") as mock_db:
            mock_db.file_uploads_collection.insert_one = AsyncMock()
            result = await upload_service.upload(file, "room1", "user1")
        assert result.file_url == "https://s3/presigned"
        assert result.mime_type == "image/png"

    async def test_invalid_mime_type(self, upload_service):
        file = _make_upload_file(content_type="application/exe")
        with pytest.raises(HTTPException) as exc_info:
            await upload_service.upload(file, "room1", "user1")
        assert exc_info.value.status_code == 415

    async def test_file_too_large(self, upload_service):
        big_content = b"\x89PNG" + b"x" * (51 * 1024 * 1024)
        file = _make_upload_file(content=big_content)
        with pytest.raises(HTTPException) as exc_info:
            await upload_service.upload(file, "room1", "user1")
        assert exc_info.value.status_code == 413

    async def test_magic_byte_mismatch(self, upload_service):
        file = _make_upload_file(content=b"\x89PNG\r\n\x1a\n" + b"x" * 100, content_type="application/pdf")
        with pytest.raises(HTTPException) as exc_info:
            await upload_service.upload(file, "room1", "user1")
        assert exc_info.value.status_code == 422

    async def test_mongodb_failure_compensating_delete(self, upload_service):
        file = _make_upload_file()
        with patch("services.file_upload_service.mongodb") as mock_db:
            mock_db.file_uploads_collection.insert_one = AsyncMock(side_effect=Exception("DB error"))
            with pytest.raises(HTTPException) as exc_info:
                await upload_service.upload(file, "room1", "user1")
            assert exc_info.value.status_code == 500
        upload_service._s3.delete_file.assert_called_once()

    def test_detect_mime_png(self, upload_service):
        assert upload_service._detect_mime(b"\x89PNG\r\n\x1a\n") == "image/png"

    def test_detect_mime_jpeg(self, upload_service):
        assert upload_service._detect_mime(b"\xff\xd8\xff" + b"x" * 10) == "image/jpeg"

    def test_detect_mime_pdf(self, upload_service):
        assert upload_service._detect_mime(b"%PDF-1.4") == "application/pdf"

    def test_detect_mime_unknown(self, upload_service):
        assert upload_service._detect_mime(b"random bytes") is None

    def test_mime_compatible_same(self, upload_service):
        assert FileUploadService._mime_compatible("image/png", "image/png") is True

    def test_mime_compatible_different_major(self, upload_service):
        assert FileUploadService._mime_compatible("image/png", "application/pdf") is False

    def test_mime_compatible_audio_mp4_ftyp(self, upload_service):
        assert FileUploadService._mime_compatible("audio/mp4", "video/mp4") is True

    def test_mime_compatible_docx_zip(self, upload_service):
        assert FileUploadService._mime_compatible(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/zip",
        ) is True

    def test_detect_mime_mp3_id3(self, upload_service):
        assert upload_service._detect_mime(b"ID3" + b"\x00" * 20) == "audio/mpeg"

    def test_detect_mime_wav(self, upload_service):
        content = b"RIFF" + b"\x00" * 4 + b"WAVE" + b"\x00" * 20
        assert upload_service._detect_mime(content) == "audio/wav"

    def test_detect_mime_webm(self, upload_service):
        assert upload_service._detect_mime(b"\x1aE\xdf\xa3" + b"\x00" * 20) == "video/webm"

    def test_detect_mime_mp4_ftyp(self, upload_service):
        content = b"\x00\x00\x00\x18ftyp" + b"\x00" * 20
        assert upload_service._detect_mime(content) == "video/mp4"

    def test_detect_mime_zip(self, upload_service):
        assert upload_service._detect_mime(b"PK\x03\x04" + b"\x00" * 20) == "application/zip"

    def test_mime_compatible_audio_webm_matroska(self, upload_service):
        assert FileUploadService._mime_compatible("audio/webm", "video/webm") is True

    def test_mime_compatible_xlsx_zip(self, upload_service):
        assert FileUploadService._mime_compatible(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/zip",
        ) is True
