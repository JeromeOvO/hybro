import base64
from unittest.mock import AsyncMock, MagicMock

import pytest

from app_shell.s3_service import S3Service


def _bind_test_a2a_artifact_storage(monkeypatch, storage) -> None:
    from a2a_adapter import artifact_storage as a2a_artifact_storage
    from common.utils import a2a_helpers

    a2a_artifact_storage.bind_a2a_storage_dependencies(storage_service=storage)
    monkeypatch.setattr(a2a_helpers, "a2a_artifact_storage", a2a_artifact_storage)


class FakeObjectStoragePort:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def upload_file(
        self,
        file_data,
        s3_key: str,
        content_type: str,
        content_length: int | None = None,
    ) -> str:
        self.calls.append(("upload_file", file_data, s3_key, content_type, content_length))
        return s3_key

    async def generate_presigned_url(
        self,
        s3_key: str,
        *,
        filename: str | None = None,
        expires_in: int | None = None,
    ) -> str:
        self.calls.append(("generate_presigned_url", s3_key, filename, expires_in))
        return f"https://files.example/{s3_key}?name={filename or ''}"

    async def batch_presigned_urls(
        self,
        s3_keys: list[str],
        *,
        filenames: dict[str, str] | None = None,
        expires_in: int | None = None,
    ) -> dict[str, str]:
        self.calls.append(("batch_presigned_urls", s3_keys, filenames, expires_in))
        return {key: f"https://files.example/{key}" for key in s3_keys}

    async def delete_file(self, s3_key: str) -> bool:
        self.calls.append(("delete_file", s3_key))
        return True

    async def head_file(self, s3_key: str) -> dict | None:
        self.calls.append(("head_file", s3_key))
        return {"content_type": "text/plain"}

    async def delete_prefix(self, prefix: str) -> int:
        self.calls.append(("delete_prefix", prefix))
        return 3

    def get_public_url(self, s3_key: str) -> str:
        self.calls.append(("get_public_url", s3_key))
        return f"https://public.example/{s3_key}"

    async def download_text(self, s3_key: str) -> str | None:
        self.calls.append(("download_text", s3_key))
        return f"text:{s3_key}"


class TestS3ServiceShim:
    async def test_unbound_s3_service_fails_fast(self):
        svc = S3Service()

        with pytest.raises(RuntimeError, match="S3Service.bind_object_storage"):
            await svc.generate_presigned_url("test/key")
        with pytest.raises(RuntimeError, match="S3Service.bind_object_storage"):
            svc.get_public_url("test/key")

    async def test_s3_service_delegates_to_bound_object_storage(self):
        delegate = FakeObjectStoragePort()
        svc = S3Service(delegate)

        assert (
            await svc.upload_file(b"data", "objects/a.txt", "text/plain", 4)
            == "objects/a.txt"
        )
        assert (
            await svc.generate_presigned_url(
                "objects/a.txt",
                filename="a.txt",
                expires_in=30,
            )
            == "https://files.example/objects/a.txt?name=a.txt"
        )
        assert await svc.batch_presigned_urls(
            ["objects/a.txt"],
            filenames={"objects/a.txt": "a.txt"},
            expires_in=30,
        ) == {"objects/a.txt": "https://files.example/objects/a.txt"}
        assert await svc.delete_file("objects/a.txt") is True
        assert await svc.head_file("objects/a.txt") == {"content_type": "text/plain"}
        assert await svc.delete_prefix("objects/") == 3
        assert svc.get_public_url("objects/a.txt") == (
            "https://public.example/objects/a.txt"
        )
        assert await svc.download_text("objects/a.txt") == "text:objects/a.txt"
        assert await svc.get_text("objects/a.txt") == "text:objects/a.txt"

        assert delegate.calls == [
            ("upload_file", b"data", "objects/a.txt", "text/plain", 4),
            ("generate_presigned_url", "objects/a.txt", "a.txt", 30),
            (
                "batch_presigned_urls",
                ["objects/a.txt"],
                {"objects/a.txt": "a.txt"},
                30,
            ),
            ("delete_file", "objects/a.txt"),
            ("head_file", "objects/a.txt"),
            ("delete_prefix", "objects/"),
            ("get_public_url", "objects/a.txt"),
            ("download_text", "objects/a.txt"),
            ("download_text", "objects/a.txt"),
        ]

    async def test_s3_service_can_bind_delegate_after_construction(self):
        delegate = FakeObjectStoragePort()
        svc = S3Service()

        svc.bind_object_storage(delegate)

        assert await svc.generate_presigned_url("objects/a.txt") == (
            "https://files.example/objects/a.txt?name="
        )
        assert delegate.calls == [
            ("generate_presigned_url", "objects/a.txt", None, None)
        ]


class TestInlineBase64ConversionErrors:
    async def test_s3_upload_failure_logs_error(self, monkeypatch):
        from execution.dispatch.transports.direct import DirectTransport

        db = MagicMock()
        processor = DirectTransport(
            response_handler=MagicMock(),
            tsm=MagicMock(),
            sse_manager=MagicMock(),
            a2a_service=MagicMock(),
            task_service=MagicMock(),
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
            sse_manager=MagicMock(),
            a2a_service=MagicMock(),
            task_service=MagicMock(),
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
        from app_shell.room_runtime import RoomServices
        from models.response import RoomCenterUserMessageResponse

        svc = RoomServices()
        svc.database_service = MagicMock()
        svc.sse_manager = MagicMock()
        svc.room_memory_service = MagicMock()

        reader = MagicMock()
        reader.get_for_room_file = AsyncMock(return_value=None)
        svc.bind_attachment_metadata_reader(reader)

        result = await svc._resolve_attachments(["nonexistent"], "room1")

        assert isinstance(result, RoomCenterUserMessageResponse)
        assert result.status_code == 404
        assert not result.success
