import base64
import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from app_shell.s3_service import S3Service


class TestS3ServiceErrors:
    async def test_upload_client_error_raises(self):
        svc = S3Service()
        error = ClientError({"Error": {"Code": "NoSuchBucket", "Message": "Not found"}}, "PutObject")
        with patch.object(svc, "_session") as mock_session:
            mock_client = AsyncMock()
            mock_client.upload_fileobj = AsyncMock(side_effect=error)
            ctx = AsyncMock()
            ctx.__aenter__ = AsyncMock(return_value=mock_client)
            ctx.__aexit__ = AsyncMock(return_value=False)
            mock_session.client.return_value = ctx
            with pytest.raises(ClientError):
                await svc.upload_file(
                    file_data=io.BytesIO(b"data"),
                    s3_key="test/key",
                    content_type="text/plain",
                    content_length=4,
                )

    async def test_presigned_url_client_error_raises(self):
        svc = S3Service()
        error = ClientError({"Error": {"Code": "AccessDenied", "Message": "denied"}}, "GetObject")
        with patch.object(svc, "_session") as mock_session:
            mock_client = AsyncMock()
            mock_client.generate_presigned_url = AsyncMock(side_effect=error)
            ctx = AsyncMock()
            ctx.__aenter__ = AsyncMock(return_value=mock_client)
            ctx.__aexit__ = AsyncMock(return_value=False)
            mock_session.client.return_value = ctx
            svc._url_cache.clear()
            with pytest.raises(ClientError):
                await svc.generate_presigned_url("test/key")

    async def test_download_text_returns_none_on_missing(self):
        svc = S3Service()
        error = ClientError({"Error": {"Code": "NoSuchKey", "Message": "not found"}}, "GetObject")
        with patch.object(svc, "_session") as mock_session:
            mock_client = AsyncMock()
            mock_client.get_object = AsyncMock(side_effect=error)
            ctx = AsyncMock()
            ctx.__aenter__ = AsyncMock(return_value=mock_client)
            ctx.__aexit__ = AsyncMock(return_value=False)
            mock_session.client.return_value = ctx
            result = await svc.download_text("missing/key")
        assert result is None


class TestInlineBase64ConversionErrors:
    async def test_s3_upload_failure_logs_error(self):
        from execution.dispatch.transports.direct import DirectTransport

        processor = DirectTransport(
            response_handler=MagicMock(),
            tsm=MagicMock(),
            sse_manager=MagicMock(),
            a2a_service=MagicMock(),
            task_service=MagicMock(),
            database_service=MagicMock(),
        )
        processor._s3_service = AsyncMock()
        processor._s3_service.upload_file = AsyncMock(side_effect=Exception("S3 down"))

        artifact = MagicMock()
        file_content = MagicMock()
        file_content.bytes = base64.b64encode(b"image data").decode()
        file_content.mime_type = "image/png"
        file_content.uri = None
        root = MagicMock()
        root.kind = "file"
        root.file = file_content
        part = MagicMock()
        part.root = root
        artifact.parts = [part]

        await processor._convert_inline_bytes_to_s3(artifact, "room1", "msg1")
        assert file_content.uri is None


class TestSharedInlineConversionCap:
    """Regression: artifact-update + message non-text parts must share a single
    per-message inline conversion cap.  Before the fix, _finalize_streaming
    started its own counter from 0, so combining both paths could exceed
    MAX_INLINE_CONVERSIONS_PER_MESSAGE."""

    async def test_total_conversions_respect_cap_across_both_paths(self):
        from models.file_upload import MAX_INLINE_CONVERSIONS_PER_MESSAGE
        from execution.dispatch.transports.direct import DirectTransport, MessageStreamingState

        upload_calls: list[str] = []

        async def fake_upload(*, file_data, s3_key, content_type, content_length):
            upload_calls.append(s3_key)

        mock_s3 = AsyncMock()
        mock_s3.upload_file = AsyncMock(side_effect=fake_upload)
        mock_s3.generate_presigned_url = AsyncMock(
            return_value="https://s3.example.com/presigned"
        )

        processor = DirectTransport(
            response_handler=MagicMock(),
            tsm=MagicMock(),
            sse_manager=MagicMock(),
            a2a_service=MagicMock(),
            task_service=MagicMock(),
            database_service=MagicMock(),
            s3_service=mock_s3,
        )

        cap = MAX_INLINE_CONVERSIONS_PER_MESSAGE
        raw_b64 = base64.b64encode(b"pixel data").decode()

        # --- Phase 1: simulate artifact-update consuming (cap - 2) conversions ---
        streaming_state = MessageStreamingState()

        from a2a.types import Artifact as A2AArtifact
        from a2a.types import FilePart, FileWithBytes, Part

        for idx in range(cap - 2):
            fp = FilePart(file=FileWithBytes(bytes=raw_b64, mime_type="image/png"))
            artifact = A2AArtifact(artifact_id=f"art-{idx}", parts=[Part(root=fp)])

            shared_counter = [streaming_state.inline_conversion_count]
            with patch("app_shell.s3_service.s3_service", mock_s3):
                await processor._convert_inline_bytes_to_s3(
                    artifact, "room1", "msg1",
                    conversion_counter=shared_counter,
                )
            streaming_state.inline_conversion_count = shared_counter[0]

        assert streaming_state.inline_conversion_count == cap - 2
        assert len(upload_calls) == cap - 2

        # --- Phase 2: finalize with 5 non-text parts → only 2 should convert ---
        non_text_parts = [
            {"kind": "file", "file": {"bytes": raw_b64, "mime_type": "image/jpeg"}}
            for _ in range(5)
        ]

        with patch(
            "app_shell.s3_service.s3_service", mock_s3
        ):
            new_total = await processor._convert_streaming_parts_to_s3(
                non_text_parts, "room1", "msg1",
                converted_so_far=streaming_state.inline_conversion_count,
            )
            streaming_state.inline_conversion_count = new_total

        total_uploads = len(upload_calls)
        assert total_uploads == cap, (
            f"Expected exactly {cap} uploads (cap), got {total_uploads}"
        )
        assert streaming_state.inline_conversion_count == cap

        unconverted = [p for p in non_text_parts if p["file"].get("bytes") is not None]
        assert len(unconverted) == 3, (
            f"Expected 3 parts left unconverted, got {len(unconverted)}"
        )


class TestMissingFileId:
    async def test_resolve_attachments_missing_file(self):
        from models.response import RoomCenterUserMessageResponse
        from app_shell.room_runtime import RoomServices

        svc = RoomServices()
        svc.database_service = MagicMock()
        svc.sse_manager = MagicMock()
        svc.room_memory_service = MagicMock()

        with patch("database.mongodb.mongodb") as mock_db:
            mock_db.file_uploads_collection.find_one = AsyncMock(return_value=None)
            result = await svc._resolve_attachments(["nonexistent"], "room1")

        assert isinstance(result, RoomCenterUserMessageResponse)
        assert result.status_code == 404
        assert not result.success
