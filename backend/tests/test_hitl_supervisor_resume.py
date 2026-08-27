"""Supervisor ask_user resume port: answer → suspended orchestrator Run."""

from __future__ import annotations

import pytest

from common.dto.execution import HITLRequest
from execution.hitl.service import ContinuationLostError, HITLService


def _request(**overrides) -> HITLRequest:
    base = dict(
        request_id="request-1",
        room_id="room-1",
        user_message_id="user-1",
        source="supervisor",
        prompt="Which city?",
        source_step_id="call-ask-1",
        orchestration_run_id="run-1",
        interaction_id="interaction-1",
    )
    base.update({key: value for key, value in overrides.items() if value is not None})
    for key in [name for name, value in overrides.items() if value is None]:
        base.pop(key, None)
    return HITLRequest(**base)


@pytest.mark.asyncio
async def test_supervisor_response_resumes_through_bound_port():
    calls: list[dict] = []

    async def resume(run_id, *, call_id, answers):
        calls.append({"run_id": run_id, "call_id": call_id, "answers": answers})
        return True

    service = HITLService(supervisor_resume=resume)
    await service._handle_supervisor_response(
        _request(),
        "Shanghai",
        effect_id="effect-1",
    )
    assert calls == [
        {"run_id": "run-1", "call_id": "call-ask-1", "answers": "Shanghai"}
    ]


@pytest.mark.asyncio
async def test_supervisor_response_requires_call_identity_and_port():
    calls: list[dict] = []

    async def resume(run_id, *, call_id, answers):
        calls.append({"run_id": run_id, "call_id": call_id, "answers": answers})

    service = HITLService(supervisor_resume=resume)
    with pytest.raises(ContinuationLostError):
        await service._handle_supervisor_response(
            _request(source_step_id=None),
            "Shanghai",
            effect_id="effect-1",
        )
    with pytest.raises(ContinuationLostError):
        await service._handle_supervisor_response(
            _request(orchestration_run_id=None),
            "Shanghai",
            effect_id="effect-1",
        )
    unbound = HITLService()
    with pytest.raises(ContinuationLostError):
        await unbound._handle_supervisor_response(
            _request(),
            "Shanghai",
            effect_id="effect-1",
        )
    assert calls == []


def test_hitl_service_keeps_closed_supervisor_resume_contract():
    service = HITLService(supervisor_resume=None)
    assert service._supervisor_resume is None
