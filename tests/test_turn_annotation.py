from models.room import UserAttachment
from services.room_services import _human_size, build_turn_content


class TestHumanSize:
    def test_bytes(self):
        assert _human_size(512) == "512B"

    def test_kilobytes(self):
        assert _human_size(245 * 1024) == "245KB"

    def test_megabytes(self):
        assert _human_size(int(1.2 * 1024 * 1024)) == "1.2MB"

    def test_zero(self):
        assert _human_size(0) == "0B"

def _make_attachment(name="photo.png", mime="image/png", size=245760):
    return UserAttachment(
        file_id="f1", s3_key="uploads/r/f1/photo.png",
        mime_type=mime, file_name=name, size_bytes=size,
    )

class TestBuildTurnContent:
    def test_text_only(self):
        assert build_turn_content("hello", None) == "hello"

    def test_empty_text_no_attachments(self):
        assert build_turn_content("", None) == ""

    def test_single_attachment(self):
        result = build_turn_content("hello", [_make_attachment()])
        assert "[Attachments: photo.png (image/png, 240KB)]" in result
        assert result.startswith("hello\n")

    def test_multiple_attachments(self):
        atts = [
            _make_attachment("a.png", "image/png", 1024),
            _make_attachment("b.pdf", "application/pdf", 2 * 1024 * 1024),
        ]
        result = build_turn_content("text", atts)
        assert "a.png" in result
        assert "b.pdf" in result

    def test_empty_text_with_attachments(self):
        result = build_turn_content("", [_make_attachment()])
        assert result.startswith("\n[Attachments:")

    def test_empty_attachments_list(self):
        assert build_turn_content("hello", []) == "hello"
