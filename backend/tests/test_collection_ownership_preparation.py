"""C5 step 1 preparation: persistence imports the timeline from the package root."""

from __future__ import annotations

from pathlib import Path

from tests.test_module_boundaries import imported_modules

BACKEND = Path(__file__).resolve().parents[1]


def test_timeline_normalizer_is_exported_from_the_conversation_root():
    import conversation
    from conversation.timeline import normalize_timeline_document

    assert conversation.normalize_timeline_document is normalize_timeline_document
    assert "normalize_timeline_document" in conversation.__all__


def test_persistence_callers_use_the_conversation_root():
    for relative in (
        "dal/orchestrator/projection.py",
        "dal/runtime_store/parts/message_store.py",
    ):
        modules = imported_modules(BACKEND / relative)
        assert "conversation.timeline" not in modules, relative
        assert "conversation" in modules, relative
