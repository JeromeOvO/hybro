from unittest.mock import MagicMock

from common.utils.a2a_helpers import (
    extract_parts,
    extract_parts_from_artifacts,
    extract_text_from_artifacts,
    get_text_from_message,
)


def _make_text_part(text):
    part = MagicMock()
    part.root.kind = "text"
    part.root.text = text
    return part

def _make_file_part(uri="https://s3/file.png", mime="image/png"):
    part = MagicMock()
    part.root.kind = "file"
    part.root.model_dump.return_value = {"kind": "file", "file": {"uri": uri, "mime_type": mime}}
    return part

def _make_data_part(data=None):
    part = MagicMock()
    part.root.kind = "data"
    part.root.model_dump.return_value = {"kind": "data", "data": data or {"key": "val"}}
    return part

class TestExtractParts:
    def test_text_only(self):
        parts = [_make_text_part("hello"), _make_text_part(" world")]
        result = extract_parts(parts)
        assert result.text == "hello world"
        assert not result.has_non_text

    def test_file_only(self):
        parts = [_make_file_part()]
        result = extract_parts(parts)
        assert result.text == ""
        assert len(result.file_parts) == 1
        assert result.has_non_text

    def test_data_only(self):
        parts = [_make_data_part()]
        result = extract_parts(parts)
        assert result.text == ""
        assert len(result.data_parts) == 1

    def test_mixed(self):
        parts = [_make_text_part("hi"), _make_file_part(), _make_data_part()]
        result = extract_parts(parts)
        assert result.text == "hi"
        assert len(result.file_parts) == 1
        assert len(result.data_parts) == 1
        assert result.has_non_text

    def test_empty(self):
        result = extract_parts([])
        assert result.text == ""
        assert not result.has_non_text

    def test_unknown_kind_with_text_fallback(self):
        part = MagicMock()
        part.root.kind = "unknown"
        part.root.text = "fallback"
        result = extract_parts([part])
        assert result.text == "fallback"

    def test_unknown_kind_no_text(self):
        part = MagicMock()
        part.root.kind = "unknown"
        part.root.text = None
        result = extract_parts([part])
        assert result.text == ""

class TestExtractPartsFromArtifacts:
    def test_single_artifact_text(self):
        artifact = MagicMock()
        artifact.parts = [_make_text_part("content")]
        result = extract_parts_from_artifacts([artifact])
        assert result.text == "content"

    def test_multiple_artifacts(self):
        a1 = MagicMock()
        a1.parts = [_make_text_part("a")]
        a2 = MagicMock()
        a2.parts = [_make_text_part("b"), _make_file_part()]
        result = extract_parts_from_artifacts([a1, a2])
        assert result.text == "ab"
        assert len(result.file_parts) == 1

    def test_empty_artifact(self):
        artifact = MagicMock()
        artifact.parts = None
        result = extract_parts_from_artifacts([artifact])
        assert result.text == ""

class TestBackwardCompat:
    def test_get_text_from_message_none(self):
        assert get_text_from_message(None) == ""

    def test_get_text_from_message_text(self):
        msg = MagicMock()
        msg.parts = [_make_text_part("hello")]
        assert get_text_from_message(msg) == "hello"

    def test_extract_text_from_artifacts_none_when_empty(self):
        artifact = MagicMock()
        artifact.parts = []
        assert extract_text_from_artifacts([artifact]) is None

    def test_extract_text_from_artifacts_text(self):
        artifact = MagicMock()
        artifact.parts = [_make_text_part("result")]
        assert extract_text_from_artifacts([artifact]) == "result"
