import base64
import logging

import pytest

from models.room import UserAttachment


class BytesReader:
    def __init__(self, values):
        self.values = values
        self.calls = []

    async def get_bytes(self, key: str, *, max_bytes: int):
        self.calls.append((key, max_bytes))
        value = self.values[key]
        if isinstance(value, Exception):
            raise value
        return value


def _attachment(
    *,
    file_id: str = "file-1",
    key: str = "uploads/room/file/report.pdf",
    name: str = "report.pdf",
    mime_type: str = "application/pdf",
    size_bytes: int = 7,
) -> UserAttachment:
    return UserAttachment(
        file_id=file_id,
        s3_key=key,
        mime_type=mime_type,
        file_name=name,
        size_bytes=size_bytes,
    )


def test_mime_type_is_accepted_cases():
    from common.utils.a2a_file_modes import mime_type_is_accepted

    assert mime_type_is_accepted("application/pdf", ["file"]) is True
    assert mime_type_is_accepted("image/png", ["*/*"]) is True
    assert mime_type_is_accepted("application/pdf", ["application/pdf"]) is True
    assert mime_type_is_accepted("image/png", ["application/pdf"]) is False
    assert mime_type_is_accepted("image/png", ["image/*"]) is True
    assert mime_type_is_accepted("image/png", ["image/"]) is True
    assert mime_type_is_accepted("application/pdf", ["text"]) is False


@pytest.mark.parametrize(
    ("raw_size", "encoded_size"),
    [(0, 0), (1, 4), (3, 4), (4, 8)],
)
def test_encoded_base64_size(raw_size, encoded_size):
    from room.a2a_file_parts import encoded_base64_size

    assert encoded_base64_size(raw_size) == encoded_size


@pytest.mark.asyncio
async def test_build_attachment_file_parts_builds_inline_bytes():
    from room.a2a_file_parts import build_attachment_file_parts

    raw = b"pdfdata"
    reader = BytesReader({"uploads/room/file/report.pdf": raw})

    result = await build_attachment_file_parts(
        attachments=[_attachment()],
        agent_card={"default_input_modes": ["application/pdf"]},
        content_reader=reader,
        max_raw_bytes=1024,
        max_encoded_bytes=1024,
    )

    assert result.failure is None
    assert len(result.parts) == 1
    file_data = result.parts[0].root.file
    assert file_data.name == "report.pdf"
    assert file_data.mimeType == "application/pdf"
    assert file_data.uri is None
    assert base64.b64decode(file_data.bytes.encode("ascii")) == raw
    assert reader.calls == [("uploads/room/file/report.pdf", 1024)]


@pytest.mark.asyncio
async def test_build_attachment_file_parts_rejects_unsupported_mime_before_read():
    from room.a2a_file_parts import build_attachment_file_parts

    reader = BytesReader({"uploads/room/file/report.pdf": b"pdfdata"})

    result = await build_attachment_file_parts(
        attachments=[_attachment()],
        agent_card={"default_input_modes": ["image/*"]},
        content_reader=reader,
        max_raw_bytes=1024,
        max_encoded_bytes=1024,
    )

    assert result.parts == []
    assert result.failure is not None
    assert result.failure.code == "agent_does_not_accept_file_type"
    assert "report.pdf" in result.failure.message
    assert reader.calls == []


@pytest.mark.asyncio
async def test_build_attachment_file_parts_rejects_mixed_set_when_any_mime_unsupported():
    from room.a2a_file_parts import build_attachment_file_parts

    reader = BytesReader(
        {
            "uploads/room/file/report.pdf": b"pdfdata",
            "uploads/room/file/photo.png": b"pngdata",
        }
    )

    result = await build_attachment_file_parts(
        attachments=[
            _attachment(),
            _attachment(
                file_id="file-2",
                key="uploads/room/file/photo.png",
                name="photo.png",
                mime_type="image/png",
            ),
        ],
        agent_card={"default_input_modes": ["application/pdf"]},
        content_reader=reader,
        max_raw_bytes=1024,
        max_encoded_bytes=1024,
    )

    assert result.parts == []
    assert result.failure is not None
    assert result.failure.code == "agent_does_not_accept_file_type"
    assert "photo.png (image/png)" in result.failure.message
    assert result.failure.file_names == ("photo.png",)
    assert reader.calls == []


@pytest.mark.asyncio
async def test_build_attachment_file_parts_rejects_declared_raw_oversize_before_read():
    from room.a2a_file_parts import build_attachment_file_parts

    reader = BytesReader({"uploads/room/file/report.pdf": b"pdfdata"})

    result = await build_attachment_file_parts(
        attachments=[_attachment(size_bytes=1025)],
        agent_card={"default_input_modes": ["application/pdf"]},
        content_reader=reader,
        max_raw_bytes=1024,
        max_encoded_bytes=4096,
    )

    assert result.parts == []
    assert result.failure is not None
    assert result.failure.code == "file_too_large"
    assert reader.calls == []


@pytest.mark.asyncio
async def test_build_attachment_file_parts_rejects_declared_aggregate_encoded_oversize():
    from room.a2a_file_parts import build_attachment_file_parts

    reader = BytesReader({"uploads/room/file/report.pdf": b"pdfdata"})

    result = await build_attachment_file_parts(
        attachments=[_attachment(size_bytes=4)],
        agent_card={"default_input_modes": ["application/pdf"]},
        content_reader=reader,
        max_raw_bytes=1024,
        max_encoded_bytes=7,
    )

    assert result.parts == []
    assert result.failure is not None
    assert result.failure.code == "message_too_large"
    assert "aggregate" in result.failure.message.lower()
    assert reader.calls == []


@pytest.mark.asyncio
async def test_build_attachment_file_parts_handles_missing_storage_bytes():
    from room.a2a_file_parts import build_attachment_file_parts

    reader = BytesReader({"uploads/room/file/report.pdf": None})

    result = await build_attachment_file_parts(
        attachments=[_attachment()],
        agent_card={"default_input_modes": ["application/pdf"]},
        content_reader=reader,
        max_raw_bytes=1024,
        max_encoded_bytes=1024,
    )

    assert result.parts == []
    assert result.failure is not None
    assert result.failure.code == "file_unavailable"


@pytest.mark.asyncio
async def test_build_attachment_file_parts_rejects_empty_storage_bytes():
    from room.a2a_file_parts import build_attachment_file_parts

    reader = BytesReader({"uploads/room/file/report.pdf": b""})

    result = await build_attachment_file_parts(
        attachments=[_attachment()],
        agent_card={"default_input_modes": ["application/pdf"]},
        content_reader=reader,
        max_raw_bytes=1024,
        max_encoded_bytes=1024,
    )

    assert result.parts == []
    assert result.failure is not None
    assert result.failure.code == "empty_file"
    assert "report.pdf" in result.failure.message


@pytest.mark.asyncio
async def test_build_attachment_file_parts_normalizes_storage_exceptions(caplog):
    from room.a2a_file_parts import (
        AttachmentDispatchContext,
        build_attachment_file_parts,
    )

    reader = BytesReader({"uploads/room/file/report.pdf": RuntimeError("storage down")})

    with caplog.at_level(logging.ERROR, logger="room.a2a_file_parts"):
        result = await build_attachment_file_parts(
            attachments=[_attachment()],
            agent_card={"default_input_modes": ["application/pdf"]},
            content_reader=reader,
            max_raw_bytes=1024,
            max_encoded_bytes=1024,
            context=AttachmentDispatchContext(
                room_id="room-1",
                message_id="msg-1",
                agent_id="agent-1",
            ),
        )

    assert result.parts == []
    assert result.failure is not None
    assert result.failure.code == "storage_unavailable"
    assert result.failure.file_names == ("report.pdf",)
    record = caplog.records[0]
    assert record.room_id == "room-1"
    assert record.message_id == "msg-1"
    assert record.agent_id == "agent-1"
    assert "pdfdata" not in caplog.text
    assert "cGRmZGF0YQ==" not in caplog.text


@pytest.mark.asyncio
async def test_build_uri_file_part_sets_uri_without_bytes():
    from room.a2a_file_parts import (
        A2AOutboundFile,
        AttachmentUriResolver,
        build_uri_file_part,
    )

    class UriResolver(AttachmentUriResolver):
        async def get_uri(self, key: str, *, filename: str | None = None) -> str:
            return f"https://files.example/{key}?filename={filename}"

    part = await build_uri_file_part(
        A2AOutboundFile(
            name="report.pdf",
            mime_type="application/pdf",
            storage_key="uploads/room/file/report.pdf",
            size_bytes=7,
        ),
        uri_resolver=UriResolver(),
    )

    file_data = part.root.file
    assert file_data.uri == (
        "https://files.example/uploads/room/file/report.pdf?filename=report.pdf"
    )
    assert file_data.bytes is None
