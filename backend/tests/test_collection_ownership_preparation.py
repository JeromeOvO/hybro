"""C5 step 1 preparation: the timeline normalizer is exported from the package root."""

from __future__ import annotations


def test_timeline_normalizer_is_exported_from_the_conversation_root():
    import conversation
    from conversation.timeline import normalize_timeline_document

    assert conversation.normalize_timeline_document is normalize_timeline_document
    assert "normalize_timeline_document" in conversation.__all__
