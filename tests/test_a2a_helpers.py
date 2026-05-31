"""Tests for common/utils/a2a_helpers.py.

Covers sanitize_artifact_parts (write-path defense), extract_parts,
extract_parts_from_artifacts, and append_artifact_to_task_dict.
"""

from unittest.mock import MagicMock

from common.utils.a2a_helpers import (
    ExtractedParts,
    append_artifact_to_task_dict,
    extract_parts,
    sanitize_artifact_parts,
)


# ---------------------------------------------------------------------------
# sanitize_artifact_parts  (write-path defense — mirrors _sanitize_parts logic)
# ---------------------------------------------------------------------------
class TestSanitizeArtifactParts:
    """Unit tests for the write-path sanitizer."""

    def test_valid_text_part_kept(self):
        parts = [{"kind": "text", "text": "hello"}]
        assert sanitize_artifact_parts(parts) == parts

    def test_valid_file_part_kept(self):
        parts = [{"kind": "file", "file": {"uri": "https://example.com/f.png"}}]
        assert sanitize_artifact_parts(parts) == parts

    def test_valid_data_part_kept(self):
        parts = [{"kind": "data", "data": {"key": "val"}}]
        assert sanitize_artifact_parts(parts) == parts

    def test_malformed_text_stripped(self):
        parts = [{"kind": "text"}]
        assert sanitize_artifact_parts(parts) == []

    def test_malformed_text_null_stripped(self):
        parts = [{"kind": "text", "text": None}]
        assert sanitize_artifact_parts(parts) == []

    def test_malformed_file_stripped(self):
        parts = [{"kind": "file"}]
        assert sanitize_artifact_parts(parts) == []

    def test_malformed_data_stripped(self):
        parts = [{"kind": "data"}]
        assert sanitize_artifact_parts(parts) == []

    def test_mixed_parts(self):
        parts = [
            {"kind": "text", "text": "ok"},
            {"kind": "text"},
            {"kind": "data", "data": {}},
            {"kind": "file"},
        ]
        result = sanitize_artifact_parts(parts)
        assert len(result) == 2
        assert result[0]["text"] == "ok"
        assert result[1]["kind"] == "data"

    def test_root_wrapper_handled(self):
        parts = [{"root": {"kind": "text"}}]
        assert sanitize_artifact_parts(parts) == []

    def test_root_wrapper_valid(self):
        parts = [{"root": {"kind": "text", "text": "hi"}}]
        assert sanitize_artifact_parts(parts) == parts

    def test_empty_text_field_is_valid(self):
        parts = [{"kind": "text", "text": ""}]
        assert sanitize_artifact_parts(parts) == parts

    def test_returns_new_list(self):
        """Result is a new list, not the original."""
        original = [{"kind": "text", "text": "a"}]
        result = sanitize_artifact_parts(original)
        assert result is not original

    def test_empty_dict_dropped(self):
        """Bare {} from protobuf Part(text='') serialization is dropped."""
        parts = [{}]
        assert sanitize_artifact_parts(parts) == []

    def test_metadata_only_part_dropped(self):
        """A part with only metadata and no content key is dropped."""
        parts = [{"metadata": {"source": "db"}}]
        assert sanitize_artifact_parts(parts) == []

    def test_unknown_kind_with_content_key_kept(self):
        """A part with unknown kind but recognized content key passes."""
        parts = [{"kind": "custom", "url": "https://example.com/f.png"}]
        assert sanitize_artifact_parts(parts) == parts

    def test_url_part_without_kind_kept(self):
        """Flat url-style part (no kind) passes the content-key check."""
        parts = [{"url": "https://example.com/doc.pdf"}]
        assert sanitize_artifact_parts(parts) == parts


# ---------------------------------------------------------------------------
# extract_parts
# ---------------------------------------------------------------------------
class TestExtractParts:
    """Unit tests for extract_parts with mock part objects."""

    @staticmethod
    def _make_part(kind: str, **kwargs):
        """Create a mock part object with .root."""
        root = MagicMock()
        root.kind = kind
        for k, v in kwargs.items():
            setattr(root, k, v)
        part = MagicMock()
        part.root = root
        return part

    def test_text_extraction(self):
        part = self._make_part("text", text="hello world")
        result = extract_parts([part])
        assert result.text == "hello world"

    def test_multiple_text_parts_concatenated(self):
        parts = [
            self._make_part("text", text="hello "),
            self._make_part("text", text="world"),
        ]
        result = extract_parts(parts)
        assert result.text == "hello world"

    def test_file_part_extraction(self):
        part = self._make_part("file", text=None)
        part.root.model_dump = MagicMock(return_value={"kind": "file", "file": {"uri": "s3://x"}})
        result = extract_parts([part])
        assert len(result.file_parts) == 1
        assert result.has_non_text is True

    def test_data_part_extraction(self):
        part = self._make_part("data", text=None)
        part.root.model_dump = MagicMock(return_value={"kind": "data", "data": {"k": "v"}})
        result = extract_parts([part])
        assert len(result.data_parts) == 1

    def test_empty_text_ignored(self):
        """Text parts with falsy text (empty string) are skipped."""
        part = self._make_part("text", text="")
        result = extract_parts([part])
        assert result.text == ""
        assert result.text_parts == []

    def test_empty_list(self):
        result = extract_parts([])
        assert result.text == ""
        assert result.text_parts == []
        assert result.file_parts == []
        assert result.data_parts == []

    def test_unknown_kind_with_text_fallback(self):
        part = self._make_part("unknown", text="fallback text")
        result = extract_parts([part])
        assert result.text == "fallback text"

    def test_unknown_kind_without_text(self):
        part = self._make_part("mystery", text=None)
        result = extract_parts([part])
        assert result.text == ""


# ---------------------------------------------------------------------------
# ExtractedParts
# ---------------------------------------------------------------------------
class TestExtractedParts:

    def test_text_joins_parts(self):
        ep = ExtractedParts(text_parts=["a", "b", "c"])
        assert ep.text == "abc"

    def test_has_non_text_false_when_empty(self):
        ep = ExtractedParts()
        assert ep.has_non_text is False

    def test_has_non_text_true_with_files(self):
        ep = ExtractedParts(file_parts=[{"kind": "file"}])
        assert ep.has_non_text is True


# ---------------------------------------------------------------------------
# append_artifact_to_task_dict
# ---------------------------------------------------------------------------
class TestAppendArtifactToTaskDict:

    def test_append_to_none_creates_list(self):
        art = {"artifactId": "a1", "parts": [{"kind": "text", "text": "x"}]}
        result = append_artifact_to_task_dict(None, art)
        assert len(result) == 1
        assert result[0]["artifactId"] == "a1"

    def test_replace_existing_by_id(self):
        existing = [{"artifactId": "a1", "parts": [{"kind": "text", "text": "old"}]}]
        new = {"artifactId": "a1", "parts": [{"kind": "text", "text": "new"}]}
        result = append_artifact_to_task_dict(existing, new, append=False)
        assert len(result) == 1
        assert result[0]["parts"][0]["text"] == "new"

    def test_add_new_artifact(self):
        existing = [{"artifactId": "a1", "parts": []}]
        new = {"artifactId": "a2", "parts": [{"kind": "text", "text": "second"}]}
        result = append_artifact_to_task_dict(existing, new, append=False)
        assert len(result) == 2

    def test_append_parts_to_existing(self):
        existing = [{"artifactId": "a1", "parts": [{"kind": "text", "text": "chunk1"}]}]
        new = {"artifactId": "a1", "parts": [{"kind": "text", "text": "chunk2"}]}
        result = append_artifact_to_task_dict(existing, new, append=True)
        assert len(result) == 1
        assert len(result[0]["parts"]) == 2

    def test_append_nonexistent_creates_new(self):
        existing = [{"artifactId": "a1", "parts": []}]
        new = {"artifactId": "a99", "parts": [{"kind": "text", "text": "x"}]}
        result = append_artifact_to_task_dict(existing, new, append=True)
        assert len(result) == 2

    def test_no_artifact_id_appends(self):
        new = {"parts": [{"kind": "text", "text": "x"}]}
        result = append_artifact_to_task_dict([], new)
        assert len(result) == 1

    def test_artifact_id_underscore_variant(self):
        """Handles artifact_id (snake_case) in addition to artifactId."""
        existing = [{"artifact_id": "a1", "parts": []}]
        new = {"artifact_id": "a1", "parts": [{"kind": "text", "text": "new"}]}
        result = append_artifact_to_task_dict(existing, new, append=False)
        assert len(result) == 1
        assert result[0]["parts"][0]["text"] == "new"
