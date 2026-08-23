"""Focused tests for the context & memory-owned resource catalog (#1)."""

from __future__ import annotations

from context_memory.resources import (
    AttachmentResource,
    ResourceCatalogSource,
    assemble_resource_catalog,
    user_text_ref_id,
)
from execution.orchestrator.a2a_runtime.resources import (
    ResourceSelectionError,
    freeze_call_manifest,
)
from execution.orchestrator.models import (
    PreparedResourceRef,
    RunResourceManifestSnapshot,
)

from ._orchestrator_a2a_helpers import binding


def test_user_text_becomes_a_context_ref():
    source = ResourceCatalogSource(
        user_message_id="msg-1", user_text="Buy cyber insurance for Acme."
    )
    entries = assemble_resource_catalog(source)
    assert entries == [
        type(entries[0])(
            ref_id="ctx:message:msg-1",
            kind="context",
            source_message_id="msg-1",
            mime_type="text/plain",
            size_bytes=len(b"Buy cyber insurance for Acme."),
            content_digest=entries[0].content_digest,
        )
    ]
    assert entries[0].ref_id == user_text_ref_id("msg-1")
    assert entries[0].content_digest


def test_blank_user_text_is_not_registered():
    source = ResourceCatalogSource(user_message_id="msg-1", user_text="   ")
    assert assemble_resource_catalog(source) == []


def test_attachments_and_artifacts_are_registered_and_deduplicated():
    source = ResourceCatalogSource(
        user_message_id="msg-1",
        user_text="task",
        attachments=[AttachmentResource("file-1", "application/pdf", 100, "digest-a")],
        artifact_refs=["https://host/file-9/content", "https://host/file-9/content"],
    )
    entries = assemble_resource_catalog(source)
    kinds = [(entry.kind, entry.ref_id) for entry in entries]
    assert kinds == [
        ("context", "ctx:message:msg-1"),
        ("attachment", "file-1"),
        ("artifact", "https://host/file-9/content"),
    ]


def test_dynamic_authorization_accepts_mid_run_artifact_without_frozen_allowlist():
    """Authorization derives from the live manifest + binding input modes, so an
    artifact registered mid-run is authorizable even though the binding's
    frozen ``compatible_resource_refs`` (run-start) does not list it."""
    manifest = RunResourceManifestSnapshot(
        manifest_id="m",
        refs=[
            PreparedResourceRef(
                ref_id="artifact-1",
                kind="artifact",
                source_message_id="message-1",
                mime_type="application/json",
                size_bytes=0,
                content_digest="",
            )
        ],
        content_digest="d",
    )
    bound = binding().model_copy(
        update={"input_modes": ["file"], "compatible_resource_refs": []}
    )
    frozen = freeze_call_manifest(
        arguments={"task": "summarize", "artifact_refs": ["artifact-1"]},
        run_manifest=manifest,
        binding=bound,
        source_room_id="room-1",
        source_room_epoch=1,
    )
    assert [ref.ref_id for ref in frozen.refs] == ["artifact-1"]


def test_dynamic_authorization_denies_incompatible_ref():
    manifest = RunResourceManifestSnapshot(
        manifest_id="m",
        refs=[
            PreparedResourceRef(
                ref_id="attachment-1",
                kind="attachment",
                source_message_id="message-1",
                mime_type="application/pdf",
                size_bytes=100,
                content_digest="digest-1",
            )
        ],
        content_digest="d",
    )
    # input_modes only accepts text, so the PDF attachment is not compatible.
    bound = binding().model_copy(update={"input_modes": ["text"]})
    import pytest

    with pytest.raises(ResourceSelectionError, match="not allowed"):
        freeze_call_manifest(
            arguments={"task": "review", "attachment_refs": ["attachment-1"]},
            run_manifest=manifest,
            binding=bound,
            source_room_id="room-1",
            source_room_epoch=1,
        )
