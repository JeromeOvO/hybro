from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api_gateway.routes.room_routes import get_canonical_agent_call_detail
from common.auth import ClerkUser
from execution.orchestrator.models import TextPart, ToolResult
from room.agent_call_detail import CanonicalAgentCallDetailService


class RunStore:
    def __init__(self, run):
        self.run = run

    async def load(self, run_id):
        return self.run if self.run is not None and self.run.run_id == run_id else None


class RoomStore:
    def __init__(self, owner):
        self.owner = owner

    async def get_room_by_room_id(self, room_id):
        return SimpleNamespace(room_id=room_id, room_owner_id=self.owner)


class ArtifactMetadataReader:
    def __init__(self, metadata):
        self.metadata = metadata
        self.calls = []

    async def get_for_room_file(self, room_id, file_id):
        self.calls.append((room_id, file_id))
        return self.metadata


def canonical_run(*, output=True, artifacts=()):
    result = (
        ToolResult(
            call_id="private-call-id",
            tool_name="private-tool-name",
            status="completed",
            content=[TextPart(text="private full output")],
            artifact_refs=list(artifacts),
        )
        if output
        else None
    )
    entry = SimpleNamespace(
        opaque_public_call_id="inv_weather_0001",
        buffered_terminal_result=result,
    )
    return SimpleNamespace(
        run_id="run-1",
        room_id="room-1",
        lifecycle_family="canonical",
        tool_batches=[SimpleNamespace(entries=[entry])],
    )


@pytest.mark.asyncio
async def test_private_detail_service_returns_output_without_private_call_identity():
    detail = await CanonicalAgentCallDetailService(RunStore(canonical_run())).get(
        room_id="room-1",
        run_id="run-1",
        public_call_id="inv_weather_0001",
    )
    assert detail is not None
    payload = detail.model_dump(mode="json")
    assert payload["output"] == "private full output"
    assert "private-call-id" not in str(payload)
    assert "private-tool-name" not in str(payload)


@pytest.mark.asyncio
async def test_private_detail_returns_authenticated_artifact_descriptors():
    detail = await CanonicalAgentCallDetailService(
        RunStore(canonical_run(artifacts=("room-file-1",)))
    ).get(
        room_id="room-1",
        run_id="run-1",
        public_call_id="inv_weather_0001",
    )

    assert detail is not None
    assert detail.model_dump(mode="json")["artifacts"] == [
        {
            "artifact_ref": "room-file-1",
            "file_id": None,
            "name": None,
            "mime_type": None,
            "size_bytes": None,
        }
    ]


@pytest.mark.asyncio
async def test_private_detail_enriches_room_owned_file_artifacts_for_preview():
    file_id = "af011190aaba4f97b459e7656bba7f7e"
    metadata_reader = ArtifactMetadataReader(
        {
            "file_name": "generated-image.png",
            "mime_type": "image/png",
            "size_bytes": 2_332_106,
        }
    )
    detail = await CanonicalAgentCallDetailService(
        RunStore(
            canonical_run(
                artifacts=(f"/api/v1/files/{file_id}/content",),
            )
        ),
        artifact_metadata_reader=metadata_reader,
    ).get(
        room_id="room-1",
        run_id="run-1",
        public_call_id="inv_weather_0001",
    )

    assert detail is not None
    assert metadata_reader.calls == [("room-1", file_id)]
    assert detail.model_dump(mode="json")["artifacts"] == [
        {
            "artifact_ref": f"/api/v1/files/{file_id}/content",
            "file_id": file_id,
            "name": "generated-image.png",
            "mime_type": "image/png",
            "size_bytes": 2_332_106,
        }
    ]


@pytest.mark.asyncio
async def test_private_detail_does_not_remap_external_artifact_urls_to_room_files():
    file_id = "af011190aaba4f97b459e7656bba7f7e"
    metadata_reader = ArtifactMetadataReader(
        {
            "file_name": "unrelated.png",
            "mime_type": "image/png",
            "size_bytes": 100,
        }
    )
    external_ref = f"https://attacker.example/files/{file_id}/content"
    detail = await CanonicalAgentCallDetailService(
        RunStore(canonical_run(artifacts=(external_ref,))),
        artifact_metadata_reader=metadata_reader,
    ).get(
        room_id="room-1",
        run_id="run-1",
        public_call_id="inv_weather_0001",
    )

    assert detail is not None
    assert metadata_reader.calls == []
    assert detail.model_dump(mode="json")["artifacts"] == [
        {
            "artifact_ref": external_ref,
            "file_id": None,
            "name": None,
            "mime_type": None,
            "size_bytes": None,
        }
    ]


@pytest.mark.asyncio
async def test_private_detail_route_authorizes_room_and_handles_missing_output():
    user = ClerkUser(user_id="owner", session_id="session-1", claims={})
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                canonical_agent_call_detail_reader=CanonicalAgentCallDetailService(
                    RunStore(canonical_run())
                )
            )
        )
    )
    detail = await get_canonical_agent_call_detail(
        "room-1",
        "run-1",
        "inv_weather_0001",
        request,
        user,
        RoomStore("owner"),
    )
    assert detail.output == "private full output"

    with pytest.raises(HTTPException) as unauthorized:
        await get_canonical_agent_call_detail(
            "room-1",
            "run-1",
            "inv_weather_0001",
            request,
            user,
            RoomStore("someone-else"),
        )
    assert unauthorized.value.status_code == 403

    request.app.state.canonical_agent_call_detail_reader = (
        CanonicalAgentCallDetailService(RunStore(canonical_run(output=False)))
    )
    with pytest.raises(HTTPException) as missing:
        await get_canonical_agent_call_detail(
            "room-1",
            "run-1",
            "inv_weather_0001",
            request,
            user,
            RoomStore("owner"),
        )
    assert missing.value.status_code == 404
