import base64
from unittest.mock import AsyncMock, MagicMock


def _bind_test_a2a_artifact_storage(monkeypatch, storage) -> None:
    from a2a_adapter import artifact_storage as a2a_artifact_storage
    from common.utils import a2a_helpers

    a2a_artifact_storage.bind_a2a_storage_dependencies(storage_service=storage)
    monkeypatch.setattr(a2a_helpers, "a2a_artifact_storage", a2a_artifact_storage)


class TestInlineBase64ConversionErrors:
    async def test_s3_upload_failure_logs_error(self, monkeypatch):
        from execution.dispatch.transports.direct import DirectTransport

        db = MagicMock()
        processor = DirectTransport(
            response_handler=MagicMock(),
            tsm=MagicMock(),
            delivery=MagicMock(),
            a2a_transport=MagicMock(),
            remote_task_reader=MagicMock(),
            message_reader=db,
            artifact_store=db,
            task_updater=db,
        )
        processor._s3_service = AsyncMock()
        processor._s3_service.upload_file = AsyncMock(side_effect=Exception("S3 down"))
        _bind_test_a2a_artifact_storage(monkeypatch, processor.object_storage)

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

    async def test_total_conversions_respect_cap_across_both_paths(self, monkeypatch):
        from execution.dispatch.transports.direct import (
            DirectTransport,
            MessageStreamingState,
        )
        from models.file_upload import MAX_INLINE_CONVERSIONS_PER_MESSAGE

        upload_calls: list[str] = []

        async def fake_upload(*, file_data, s3_key, content_type, content_length):
            upload_calls.append(s3_key)

        mock_s3 = AsyncMock()
        mock_s3.upload_file = AsyncMock(side_effect=fake_upload)
        mock_s3.generate_presigned_url = AsyncMock(
            return_value="https://s3.example.com/presigned"
        )

        db = MagicMock()
        processor = DirectTransport(
            response_handler=MagicMock(),
            tsm=MagicMock(),
            delivery=MagicMock(),
            a2a_transport=MagicMock(),
            remote_task_reader=MagicMock(),
            message_reader=db,
            artifact_store=db,
            task_updater=db,
            s3_service=mock_s3,
        )
        _bind_test_a2a_artifact_storage(monkeypatch, processor.object_storage)

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
            await processor._convert_inline_bytes_to_s3(
                artifact,
                "room1",
                "msg1",
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

        new_total = await processor._convert_streaming_parts_to_s3(
            non_text_parts,
            "room1",
            "msg1",
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
        from room.compat.runtime import RoomServices

        svc = RoomServices()
        svc.database_service = MagicMock()
        svc.delivery = MagicMock()

        reader = MagicMock()
        reader.get_for_room_file = AsyncMock(return_value=None)
        svc.bind_attachment_metadata_reader(reader)

        result = await svc._resolve_attachments(["nonexistent"], "room1")

        assert isinstance(result, RoomCenterUserMessageResponse)
        assert result.status_code == 404
        assert not result.success
