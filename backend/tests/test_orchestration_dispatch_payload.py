from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from execution.orchestration.dispatch_payload import (
    DispatchPayloadValidationError,
    resolve_dispatch_payload_refs,
)
from execution.orchestration.resources import ResourcePayload, ResourceProjectionRef
from models.orchestration import (
    AgentOutputRecord,
    DelegationOutcomeRecord,
    DispatchContentRef,
    DispatchExpectedOutput,
    DispatchIntent,
    DispatchRefKind,
    OrchestrationRunState,
)
from models.room import UserAttachment


def _state():
    return OrchestrationRunState(
        run_id="run-1",
        room_id="room-1",
        user_message_id="user-msg-1",
        goal="Underwrite the submission",
        candidate_agent_ids=["agent-1"],
        artifacts=[
            {
                "artifact_key": "broker-msg:artifact_id:submission",
                "name": "Broker submission",
                "mime_type": "application/json",
                "summary": "Structured broker submission.",
                "parts": [
                    {
                        "kind": "data",
                        "data": {
                            "client": {"name": "Acme SaaS Inc."},
                            "requested_coverage": {"currency": "GBP"},
                        },
                    }
                ],
            }
        ],
        facts=[
            {
                "fact_id": "fact-1",
                "text": "Prior agent found a replacement cost of 1.2M.",
            }
        ],
    )


@pytest.mark.asyncio
async def test_resolver_returns_only_explicit_attachment_refs_supported_by_agent_card():
    attachment = UserAttachment(
        file_id="file-1",
        s3_key="uploads/room-1/file-1/report.pdf",
        mime_type="application/pdf",
        file_name="report.pdf",
        size_bytes=16,
    )
    payload = await resolve_dispatch_payload_refs(
        run_state=_state(),
        target_agent_card=SimpleNamespace(default_input_modes=["application/pdf"]),
        context_refs=[],
        artifact_refs=[],
        attachment_refs=[
            DispatchContentRef(kind=DispatchRefKind.ATTACHMENT, ref_id="file-1")
        ],
        original_attachments=[attachment],
    )

    assert payload.selected_attachment_refs == ["file-1"]
    assert payload.selected_artifact_refs == []
    assert payload.attachment_failures == []


@pytest.mark.asyncio
async def test_resolver_materializes_required_resource_refs_for_dispatch():
    attachment = UserAttachment(
        file_id="file-1",
        s3_key="uploads/room-1/file-1/report.pdf",
        mime_type="application/pdf",
        file_name="report.pdf",
        size_bytes=16,
    )
    provider = SimpleNamespace(
        resolve_ref=AsyncMock(
            return_value=ResourcePayload(
                ref_id="ctx:file-file-1:text",
                kind="context",
                mime_type="text/plain",
                text="Projected submission text",
            )
        )
    )

    payload = await resolve_dispatch_payload_refs(
        run_state=_state(),
        target_agent_card=SimpleNamespace(default_input_modes=["application/pdf"]),
        context_refs=[],
        artifact_refs=[],
        attachment_refs=[],
        required_resource_refs=[
            "ctx:file-file-1:text",
            "broker-msg:artifact_id:submission",
            "file:file-1",
        ],
        original_attachments=[attachment],
        resource_provider=provider,
    )

    assert payload.selected_context_refs == ["ctx:file-file-1:text"]
    assert payload.selected_artifact_refs == ["broker-msg:artifact_id:submission"]
    assert payload.selected_attachment_refs == ["file-1"]
    provider.resolve_ref.assert_awaited_once_with(
        "ctx:file-file-1:text",
        run_id="run-1",
        attachments=[attachment],
    )


@pytest.mark.asyncio
async def test_required_resource_ref_upgrades_matching_optional_ref():
    optional_ref = DispatchContentRef(
        kind=DispatchRefKind.CONTEXT,
        ref_id="missing",
        required=False,
    )
    with pytest.raises(
        DispatchPayloadValidationError,
        match="Context ref not found",
    ) as exc_info:
        await resolve_dispatch_payload_refs(
            run_state=_state(),
            target_agent_card=SimpleNamespace(default_input_modes=["text"]),
            context_refs=[optional_ref],
            artifact_refs=[],
            attachment_refs=[],
            required_resource_refs=["missing"],
            original_attachments=[],
        )

    assert exc_info.value.code == "context_ref_not_found"
    assert optional_ref.required is False


@pytest.mark.asyncio
async def test_resolver_rejects_unreferenced_original_attachment():
    attachment = UserAttachment(
        file_id="file-1",
        s3_key="uploads/room-1/file-1/report.pdf",
        mime_type="application/pdf",
        file_name="report.pdf",
        size_bytes=16,
    )
    payload = await resolve_dispatch_payload_refs(
        run_state=_state(),
        target_agent_card=SimpleNamespace(default_input_modes=["application/pdf"]),
        context_refs=[],
        artifact_refs=[],
        attachment_refs=[],
        original_attachments=[attachment],
    )

    assert payload.selected_attachment_refs == []
    assert payload.attachment_failures == []


@pytest.mark.asyncio
async def test_resolver_returns_projection_failure_for_incompatible_attachment():
    attachment = UserAttachment(
        file_id="file-1",
        s3_key="uploads/room-1/file-1/report.pdf",
        mime_type="application/pdf",
        file_name="report.pdf",
        size_bytes=16,
    )
    payload = await resolve_dispatch_payload_refs(
        run_state=_state(),
        target_agent_card=SimpleNamespace(default_input_modes=["text"]),
        context_refs=[],
        artifact_refs=[],
        attachment_refs=[
            DispatchContentRef(kind=DispatchRefKind.ATTACHMENT, ref_id="file-1")
        ],
        original_attachments=[attachment],
    )

    assert payload.selected_attachment_refs == []
    assert payload.attachment_failures == [
        {
            "ref_id": "file-1",
            "code": "attachment_projection_unavailable",
            "message": (
                "Attachment projection unavailable for report.pdf (application/pdf)."
            ),
        }
    ]


@pytest.mark.asyncio
async def test_resolver_rejects_unknown_required_artifact_ref():
    with pytest.raises(
        DispatchPayloadValidationError,
        match="unknown artifact",
    ) as exc_info:
        await resolve_dispatch_payload_refs(
            run_state=_state(),
            target_agent_card=SimpleNamespace(default_input_modes=["text"]),
            context_refs=[],
            artifact_refs=[
                DispatchContentRef(kind=DispatchRefKind.ARTIFACT, ref_id="missing")
            ],
            attachment_refs=[],
            original_attachments=[],
        )

    assert exc_info.value.code == "artifact_ref_not_found"


@pytest.mark.asyncio
async def test_resolver_materializes_selected_artifact_parts_for_dispatch():
    artifact_key = "broker-msg:artifact_id:submission"

    payload = await resolve_dispatch_payload_refs(
        run_state=_state(),
        target_agent_card=SimpleNamespace(
            default_input_modes=["text/plain", "application/json"]
        ),
        context_refs=[],
        artifact_refs=[
            DispatchContentRef(
                kind=DispatchRefKind.ARTIFACT,
                ref_id=artifact_key,
                required=True,
            )
        ],
        attachment_refs=[],
        original_attachments=[],
    )

    assert payload.selected_artifact_refs == [artifact_key]
    assert len(payload.resource_payloads) == 1
    resource = payload.resource_payloads[0]
    assert resource.ref_id == artifact_key
    assert resource.kind == "artifact"
    assert resource.mime_type == "application/json"
    assert resource.data == {
        "client": {"name": "Acme SaaS Inc."},
        "requested_coverage": {"currency": "GBP"},
    }
    assert resource.metadata["artifact_name"] == "Broker submission"


@pytest.mark.asyncio
async def test_resolver_rejects_unknown_required_context_ref():
    with pytest.raises(
        DispatchPayloadValidationError,
        match="Context ref not found",
    ) as exc_info:
        await resolve_dispatch_payload_refs(
            run_state=_state(),
            target_agent_card=SimpleNamespace(default_input_modes=["text"]),
            context_refs=[
                DispatchContentRef(
                    kind=DispatchRefKind.CONTEXT,
                    ref_id="missing",
                )
            ],
            artifact_refs=[],
            attachment_refs=[],
            original_attachments=[],
        )

    assert exc_info.value.code == "context_ref_not_found"


@pytest.mark.asyncio
async def test_resolver_promotes_misclassified_artifact_context_ref():
    payload = await resolve_dispatch_payload_refs(
        run_state=_state(),
        target_agent_card=SimpleNamespace(default_input_modes=["text"]),
        context_refs=[
            DispatchContentRef(
                kind=DispatchRefKind.CONTEXT,
                ref_id="broker-msg:artifact_id:submission",
            )
        ],
        artifact_refs=[],
        attachment_refs=[],
        original_attachments=[],
    )

    assert payload.selected_context_refs == []
    assert payload.selected_artifact_refs == ["broker-msg:artifact_id:submission"]


@pytest.mark.asyncio
async def test_resolver_returns_failure_for_unknown_required_attachment_ref():
    payload = await resolve_dispatch_payload_refs(
        run_state=_state(),
        target_agent_card=SimpleNamespace(default_input_modes=["text"]),
        context_refs=[],
        artifact_refs=[],
        attachment_refs=[
            DispatchContentRef(
                kind=DispatchRefKind.ATTACHMENT,
                ref_id="file:missing",
            )
        ],
        original_attachments=[],
    )

    assert payload.attachment_failures == [
        {
            "ref_id": "file:missing",
            "code": "attachment_ref_not_found",
            "message": "Attachment ref not found: file:missing.",
        }
    ]


@pytest.mark.asyncio
async def test_resolver_materializes_selected_projected_resource():
    provider = SimpleNamespace(
        resolve_ref=AsyncMock(
            return_value=ResourcePayload(
                ref_id="ctx:file-file-1:text",
                kind="context",
                mime_type="text/plain",
                text="Projected submission text",
            )
        )
    )

    payload = await resolve_dispatch_payload_refs(
        run_state=_state(),
        target_agent_card=SimpleNamespace(default_input_modes=["text"]),
        context_refs=[
            DispatchContentRef(
                kind=DispatchRefKind.CONTEXT,
                ref_id="ctx:file-file-1:text",
            )
        ],
        artifact_refs=[],
        attachment_refs=[],
        original_attachments=[],
        resource_provider=provider,
    )

    assert payload.selected_context_refs == ["ctx:file-file-1:text"]
    assert payload.resource_payloads[0].text == "Projected submission text"
    provider.resolve_ref.assert_awaited_once_with(
        "ctx:file-file-1:text",
        run_id="run-1",
        attachments=[],
    )


@pytest.mark.asyncio
async def test_resolver_omits_unknown_optional_context_ref():
    payload = await resolve_dispatch_payload_refs(
        run_state=_state(),
        target_agent_card=SimpleNamespace(default_input_modes=["text"]),
        context_refs=[
            DispatchContentRef(
                kind=DispatchRefKind.CONTEXT,
                ref_id="fact-1",
            ),
            DispatchContentRef(
                kind=DispatchRefKind.CONTEXT,
                ref_id="missing",
                required=False,
            ),
        ],
        artifact_refs=[],
        attachment_refs=[],
        original_attachments=[],
    )

    assert payload.selected_context_refs == ["fact-1"]


class FakeResourceProvider:
    async def resolve_ref(self, ref_id, *, run_id, attachments):
        assert run_id == "run-1"
        if ref_id == "ctx:file-file-1:text":
            return ResourcePayload(
                ref_id=ref_id,
                kind="context",
                mime_type="text/plain",
                text="Extracted submission text",
                summary="Extracted 25 characters from 1 PDF page.",
                metadata={"source_ref_id": "file:file-1"},
            )
        raise KeyError(ref_id)


@pytest.mark.asyncio
async def test_resolver_does_not_add_source_attachment_for_text_projection():
    attachment = UserAttachment(
        file_id="file-1",
        s3_key="uploads/room-1/file-1/report.pdf",
        mime_type="application/pdf",
        file_name="report.pdf",
        size_bytes=16,
    )

    payload = await resolve_dispatch_payload_refs(
        run_state=_state(),
        target_agent_card=SimpleNamespace(
            default_input_modes=["text/plain", "application/pdf"]
        ),
        context_refs=[
            DispatchContentRef(
                kind=DispatchRefKind.CONTEXT,
                ref_id="ctx:file-file-1:text",
                mime_type="text/plain",
            )
        ],
        artifact_refs=[],
        attachment_refs=[],
        original_attachments=[attachment],
        resource_provider=FakeResourceProvider(),
    )

    assert payload.selected_context_refs == ["ctx:file-file-1:text"]
    assert payload.selected_attachment_refs == []
    assert payload.attachment_failures == []


@pytest.mark.asyncio
async def test_required_context_ref_rejects_raw_attachment_resource_kind():
    provider = SimpleNamespace(
        resolve_ref=AsyncMock(
            return_value=ResourcePayload(
                ref_id="file:file-1",
                kind="attachment",
                mime_type="application/pdf",
                summary="Raw PDF attachment",
            )
        )
    )

    with pytest.raises(DispatchPayloadValidationError) as exc_info:
        await resolve_dispatch_payload_refs(
            run_state=_state(),
            target_agent_card=SimpleNamespace(default_input_modes=["text"]),
            context_refs=[
                DispatchContentRef(
                    kind=DispatchRefKind.CONTEXT,
                    ref_id="file:file-1",
                )
            ],
            artifact_refs=[],
            attachment_refs=[],
            original_attachments=[],
            resource_provider=provider,
        )

    assert exc_info.value.code == "context_ref_wrong_kind"


@pytest.mark.asyncio
async def test_required_context_ref_can_fall_back_from_attachment_ref_to_text_projection():
    class AttachmentProjectionProvider:
        async def resolve_ref(self, ref_id, *, run_id, attachments):
            assert run_id == "run-1"
            if ref_id == "file:file-1":
                return ResourcePayload(
                    ref_id=ref_id,
                    kind="attachment",
                    mime_type="application/pdf",
                    summary="Raw PDF attachment",
                )
            if ref_id == "ctx:file-file-1:text":
                return ResourcePayload(
                    ref_id=ref_id,
                    kind="context",
                    mime_type="text/plain",
                    text="Projected PDF text",
                )
            raise KeyError(ref_id)

        async def ensure_projection(
            self,
            ref_id,
            *,
            run_id,
            attachments,
            target_mime="text/plain",
        ):
            assert run_id == "run-1"
            assert ref_id == "file:file-1"
            return ResourceProjectionRef(
                ref_id="ctx:file-file-1:text",
                kind="context",
                source_ref_id="file:file-1",
                mime_type=target_mime,
                status="ready",
                recommended_for_input_modes=["text"],
            )

    payload = await resolve_dispatch_payload_refs(
        run_state=_state(),
        target_agent_card=SimpleNamespace(default_input_modes=["text"]),
        context_refs=[
            DispatchContentRef(
                kind=DispatchRefKind.CONTEXT,
                ref_id="file:file-1",
            )
        ],
        artifact_refs=[],
        attachment_refs=[],
        original_attachments=[
            UserAttachment(
                file_id="file-1",
                s3_key="uploads/room-1/file-1/report.pdf",
                mime_type="application/pdf",
                file_name="report.pdf",
                size_bytes=16,
            )
        ],
        resource_provider=AttachmentProjectionProvider(),
    )

    assert payload.selected_context_refs == ["ctx:file-file-1:text"]
    assert len(payload.resource_payloads) == 1
    assert payload.resource_payloads[0].text == "Projected PDF text"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mime_type", "text", "expected_code"),
    [
        ("application/pdf", "not usable as text context", "context_ref_not_text"),
        ("text/plain", "   ", "context_ref_empty"),
    ],
)
async def test_required_context_ref_rejects_unusable_context_payload(
    mime_type,
    text,
    expected_code,
):
    provider = SimpleNamespace(
        resolve_ref=AsyncMock(
            return_value=ResourcePayload(
                ref_id="ctx:file-file-1:text",
                kind="context",
                mime_type=mime_type,
                text=text,
            )
        )
    )

    with pytest.raises(DispatchPayloadValidationError) as exc_info:
        await resolve_dispatch_payload_refs(
            run_state=_state(),
            target_agent_card=SimpleNamespace(default_input_modes=["text"]),
            context_refs=[
                DispatchContentRef(
                    kind=DispatchRefKind.CONTEXT,
                    ref_id="ctx:file-file-1:text",
                )
            ],
            artifact_refs=[],
            attachment_refs=[],
            original_attachments=[],
            resource_provider=provider,
        )

    assert exc_info.value.code == expected_code


@pytest.mark.asyncio
async def test_optional_context_ref_omits_raw_attachment_resource_kind():
    provider = SimpleNamespace(
        resolve_ref=AsyncMock(
            return_value=ResourcePayload(
                ref_id="file:file-1",
                kind="attachment",
                mime_type="application/pdf",
                summary="Raw PDF attachment",
            )
        )
    )

    payload = await resolve_dispatch_payload_refs(
        run_state=_state(),
        target_agent_card=SimpleNamespace(default_input_modes=["text"]),
        context_refs=[
            DispatchContentRef(
                kind=DispatchRefKind.CONTEXT,
                ref_id="file:file-1",
                required=False,
            )
        ],
        artifact_refs=[],
        attachment_refs=[],
        original_attachments=[],
        resource_provider=provider,
    )

    assert payload.selected_context_refs == []
    assert payload.resource_payloads == []


@pytest.mark.asyncio
async def test_resolver_rejects_large_projection_context():
    provider = SimpleNamespace(
        resolve_ref=AsyncMock(
            return_value=ResourcePayload(
                ref_id="ctx:file-file-1:text",
                kind="context",
                mime_type="text/plain",
                text="x" * 100,
                summary="Large projection",
            )
        )
    )

    with pytest.raises(DispatchPayloadValidationError) as exc_info:
        await resolve_dispatch_payload_refs(
            run_state=_state(),
            target_agent_card=SimpleNamespace(default_input_modes=["text"]),
            context_refs=[
                DispatchContentRef(
                    kind=DispatchRefKind.CONTEXT,
                    ref_id="ctx:file-file-1:text",
                )
            ],
            artifact_refs=[],
            attachment_refs=[],
            original_attachments=[],
            resource_provider=provider,
            max_resource_text_chars=20,
        )

    assert exc_info.value.code == "resource_payload_too_large"


def _pdf_attachment() -> UserAttachment:
    return UserAttachment(
        file_id="file-1",
        s3_key="uploads/room-1/file-1/submission.pdf",
        mime_type="application/pdf",
        file_name="submission.pdf",
        size_bytes=256,
    )


class ProjectionProvider:
    def __init__(
        self,
        *,
        projection_status: str = "ready",
        source_ref_id: str = "file:file-1",
        projection_text: str = "Projected submission text",
    ) -> None:
        self.projection_status = projection_status
        self.source_ref_id = source_ref_id
        self.projection_text = projection_text

    async def ensure_projection(
        self,
        ref_id,
        *,
        run_id,
        attachments,
        target_mime="text/plain",
    ):
        assert run_id == "run-1"
        return ResourceProjectionRef(
            ref_id="ctx:file-file-1:text",
            kind="context",
            source_ref_id=self.source_ref_id,
            mime_type=target_mime,
            status=self.projection_status,
        )

    async def resolve_ref(self, ref_id, *, run_id, attachments):
        assert run_id == "run-1"
        if ref_id == "ctx:file-file-1:text":
            return ResourcePayload(
                ref_id=ref_id,
                kind="context",
                mime_type="text/plain",
                text=self.projection_text,
                metadata={"source_ref_id": self.source_ref_id},
            )
        raise KeyError(ref_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("input_modes", [["text"], ["text/plain"]])
async def test_attachment_projection_accepts_generic_and_mime_text_input_modes(
    input_modes,
):
    payload = await resolve_dispatch_payload_refs(
        run_state=_state(),
        target_agent_card=SimpleNamespace(default_input_modes=input_modes),
        context_refs=[],
        artifact_refs=[],
        attachment_refs=[
            DispatchContentRef(
                kind=DispatchRefKind.ATTACHMENT,
                ref_id="file:file-1",
                required=True,
            )
        ],
        original_attachments=[_pdf_attachment()],
        resource_provider=ProjectionProvider(),
    )

    assert payload.selected_context_refs == ["ctx:file-file-1:text"]
    assert payload.attachment_failures == []


@pytest.mark.asyncio
async def test_attachment_projection_default_limit_matches_projection_service():
    payload = await resolve_dispatch_payload_refs(
        run_state=_state(),
        target_agent_card=SimpleNamespace(default_input_modes=["text"]),
        context_refs=[],
        artifact_refs=[],
        attachment_refs=[
            DispatchContentRef(
                kind=DispatchRefKind.ATTACHMENT,
                ref_id="file:file-1",
                required=True,
            )
        ],
        original_attachments=[_pdf_attachment()],
        resource_provider=ProjectionProvider(projection_text="x" * 100_000),
    )

    assert payload.selected_context_refs == ["ctx:file-file-1:text"]
    assert len(payload.resource_payloads[0].text or "") == 100_000


@pytest.mark.asyncio
async def test_required_attachment_projection_raises_when_text_is_too_large():
    with pytest.raises(DispatchPayloadValidationError) as exc_info:
        await resolve_dispatch_payload_refs(
            run_state=_state(),
            target_agent_card=SimpleNamespace(default_input_modes=["text"]),
            context_refs=[],
            artifact_refs=[],
            attachment_refs=[
                DispatchContentRef(
                    kind=DispatchRefKind.ATTACHMENT,
                    ref_id="file:file-1",
                    required=True,
                )
            ],
            original_attachments=[_pdf_attachment()],
            resource_provider=ProjectionProvider(projection_text="x" * 100),
            max_resource_text_chars=20,
        )

    assert exc_info.value.code == "resource_payload_too_large"


@pytest.mark.asyncio
async def test_attachment_projection_rejects_card_incompatible_text_payload():
    payload = await resolve_dispatch_payload_refs(
        run_state=_state(),
        target_agent_card=SimpleNamespace(default_input_modes=["application/json"]),
        context_refs=[],
        artifact_refs=[],
        attachment_refs=[
            DispatchContentRef(
                kind=DispatchRefKind.ATTACHMENT,
                ref_id="file:file-1",
                required=True,
            )
        ],
        original_attachments=[_pdf_attachment()],
        resource_provider=ProjectionProvider(),
    )

    assert payload.selected_context_refs == []
    assert payload.resource_payloads == []
    assert [failure["code"] for failure in payload.attachment_failures] == [
        "agent_does_not_accept_file_type"
    ]


@pytest.mark.asyncio
async def test_required_attachment_without_projection_provider_reports_bind_failure():
    payload = await resolve_dispatch_payload_refs(
        run_state=_state(),
        target_agent_card=SimpleNamespace(default_input_modes=["text"]),
        context_refs=[],
        artifact_refs=[],
        attachment_refs=[
            DispatchContentRef(
                kind=DispatchRefKind.ATTACHMENT,
                ref_id="file:file-1",
                required=True,
            )
        ],
        original_attachments=[_pdf_attachment()],
    )

    assert payload.attachment_failures == [
        {
            "ref_id": "file:file-1",
            "code": "attachment_projection_unavailable",
            "message": (
                "Attachment projection unavailable for "
                "submission.pdf (application/pdf)."
            ),
        }
    ]


@pytest.mark.asyncio
async def test_attachment_projection_reraises_unexpected_provider_errors():
    provider = ProjectionProvider()
    provider.ensure_projection = AsyncMock(side_effect=RuntimeError("provider bug"))

    with pytest.raises(RuntimeError, match="provider bug"):
        await resolve_dispatch_payload_refs(
            run_state=_state(),
            target_agent_card=SimpleNamespace(default_input_modes=["text"]),
            context_refs=[],
            artifact_refs=[],
            attachment_refs=[
                DispatchContentRef(
                    kind=DispatchRefKind.ATTACHMENT,
                    ref_id="file:file-1",
                    required=True,
                )
            ],
            original_attachments=[_pdf_attachment()],
            resource_provider=provider,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("projection_status", "source_ref_id"),
    [("processing", "file:file-1"), ("ready", "file:other-file")],
)
async def test_attachment_projection_requires_ready_matching_context_record(
    projection_status,
    source_ref_id,
):
    payload = await resolve_dispatch_payload_refs(
        run_state=_state(),
        target_agent_card=SimpleNamespace(default_input_modes=["text"]),
        context_refs=[],
        artifact_refs=[],
        attachment_refs=[
            DispatchContentRef(
                kind=DispatchRefKind.ATTACHMENT,
                ref_id="file:file-1",
                required=True,
            )
        ],
        original_attachments=[_pdf_attachment()],
        resource_provider=ProjectionProvider(
            projection_status=projection_status,
            source_ref_id=source_ref_id,
        ),
    )

    assert payload.selected_context_refs == []
    assert payload.resource_payloads == []
    assert [failure["code"] for failure in payload.attachment_failures] == [
        "attachment_projection_unavailable"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "resource_provider",
    [None, ProjectionProvider(projection_text="x" * 100)],
)
async def test_optional_incompatible_attachment_is_omitted_without_failure(
    resource_provider,
):
    payload = await resolve_dispatch_payload_refs(
        run_state=_state(),
        target_agent_card=SimpleNamespace(default_input_modes=["text"]),
        context_refs=[],
        artifact_refs=[],
        attachment_refs=[
            DispatchContentRef(
                kind=DispatchRefKind.ATTACHMENT,
                ref_id="file:file-1",
                required=False,
            )
        ],
        original_attachments=[_pdf_attachment()],
        resource_provider=resource_provider,
        max_resource_text_chars=20,
    )

    assert payload.selected_attachment_refs == []
    assert payload.selected_context_refs == []
    assert payload.resource_payloads == []
    assert payload.attachment_failures == []


@pytest.mark.asyncio
async def test_attachment_aliases_and_explicit_projection_merge_without_duplicates():
    payload = await resolve_dispatch_payload_refs(
        run_state=_state(),
        target_agent_card=SimpleNamespace(default_input_modes=["text"]),
        context_refs=[
            DispatchContentRef(
                kind=DispatchRefKind.CONTEXT,
                ref_id="ctx:file-file-1:text",
            )
        ],
        artifact_refs=[],
        attachment_refs=[
            DispatchContentRef(
                kind=DispatchRefKind.ATTACHMENT,
                ref_id="file-1",
                required=True,
            ),
            DispatchContentRef(
                kind=DispatchRefKind.ATTACHMENT,
                ref_id="file:file-1",
                required=True,
            ),
        ],
        original_attachments=[_pdf_attachment()],
        resource_provider=ProjectionProvider(),
    )

    assert payload.selected_context_refs == ["ctx:file-file-1:text"]
    assert [resource.ref_id for resource in payload.resource_payloads] == [
        "ctx:file-file-1:text"
    ]


@pytest.mark.asyncio
async def test_attachment_aliases_select_a_compatible_raw_file_once():
    payload = await resolve_dispatch_payload_refs(
        run_state=_state(),
        target_agent_card=SimpleNamespace(default_input_modes=["application/pdf"]),
        context_refs=[],
        artifact_refs=[],
        attachment_refs=[
            DispatchContentRef(kind=DispatchRefKind.ATTACHMENT, ref_id="file-1"),
            DispatchContentRef(
                kind=DispatchRefKind.ATTACHMENT,
                ref_id="file:file-1",
            ),
        ],
        original_attachments=[_pdf_attachment()],
    )

    assert payload.selected_attachment_refs == ["file-1"]


@pytest.mark.asyncio
async def test_resolver_aliases_output_key_context_ref_to_text_evidence():
    state = OrchestrationRunState(
        run_id="run-1",
        room_id="room-1",
        user_message_id="user-msg-1",
        goal="story and image",
        candidate_agent_ids=["story", "image"],
        facts=[
            {
                "fact_id": "story-msg:text_evidence",
                "kind": "agent_text_evidence",
                "value": "Once upon a time in Technopolis...",
                "source_agent_message_id": "story-msg",
            }
        ],
        dispatch_intents=[
            DispatchIntent(
                step_id="step-1",
                step_target_id="step-1:target-1",
                dispatch_intent_id="story-intent",
                planned_agent_message_id="story-msg",
                agent_id="story",
                task="Write a story",
                task_hash="hash-story",
                expected_outputs=[
                    DispatchExpectedOutput(
                        output_key="story_text",
                        kind="text",
                        required=True,
                    )
                ],
                status="success",
            )
        ],
        delegation_outcomes=[
            DelegationOutcomeRecord(
                outcome_id="outcome-story",
                dispatch_intent_id="story-intent",
                agent_id="story",
                goal_family_fingerprint="family-story",
                goal_revision_fingerprint="revision-story",
                attempt_fingerprint="attempt-story",
                status="fulfilled",
                satisfied_output_keys=["story_text"],
            )
        ],
        agent_outputs=[
            AgentOutputRecord(
                agent_message_id="story-msg",
                agent_id="story",
                status="completed",
                text="Once upon a time in Technopolis...",
            )
        ],
    )

    payload = await resolve_dispatch_payload_refs(
        run_state=state,
        target_agent_card=SimpleNamespace(default_input_modes=["text"]),
        context_refs=[
            DispatchContentRef(
                kind=DispatchRefKind.CONTEXT,
                ref_id="story_text",
                source_agent_message_id="story-msg",
                mime_type="text/plain",
            )
        ],
        artifact_refs=[],
        attachment_refs=[],
        original_attachments=[],
    )

    assert payload.selected_context_refs == ["story-msg:text_evidence"]
    assert payload.resource_payloads[0].text.startswith("Once upon a time")
