from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from execution.orchestrator_routing import (
    DualRuntimeRouter,
    _observation_from_webhook_payload,
)

from ._orchestrator_a2a_helpers import ledger_record

TYPED_HITL_WEBHOOK_PAYLOAD = {
    "id": "1",
    "jsonrpc": "2.0",
    "result": {
        "contextId": "ctx-1",
        "final": True,
        "kind": "status-update",
        "status": {
            "message": {
                "parts": [
                    {
                        "kind": "text",
                        "text": "How many days will you stay in NYC?",
                    }
                ],
                "role": "agent",
                "metadata": {
                    "hybro.ai/a2a/interaction": {
                        "schema_version": 1,
                        "interaction_id": "travel-planner:abc123",
                        "questions": [
                            {
                                "question_id": "travel-details:abc123",
                                "interaction_kind": "questionnaire",
                                "prompt": "How many days will you stay in NYC?",
                                "answer_kind": "text",
                                "required": True,
                            }
                        ],
                    }
                },
            },
            "state": "input-required",
        },
        "taskId": "task-1",
    },
}


def test_observation_from_webhook_payload_preserves_typed_hitl_metadata():
    call = ledger_record().model_copy(
        update={
            "a2a_task_id": "task-1",
            "a2a_context_id": "ctx-1",
        }
    )

    observation = _observation_from_webhook_payload(TYPED_HITL_WEBHOOK_PAYLOAD, call)

    assert observation.event_kind == "input_required"
    assert observation.interaction_spec is not None
    assert observation.interaction_spec["interaction_id"] == "travel-planner:abc123"
    assert observation.task_id == "task-1"
    assert observation.context_id == "ctx-1"


@pytest.mark.asyncio
async def test_route_webhook_records_typed_hitl_observation():
    call = ledger_record().model_copy(
        update={
            "a2a_task_id": "task-1",
            "a2a_context_id": "ctx-1",
        }
    )
    recorded: list[object] = []
    runtime = SimpleNamespace(
        observation_ingress=SimpleNamespace(
            record=AsyncMock(side_effect=lambda obs: recorded.append(obs))
        ),
        call_ledger=SimpleNamespace(
            find_by_task_id=AsyncMock(return_value=call),
            load_by_record_id=AsyncMock(return_value=None),
        ),
    )
    router = DualRuntimeRouter(
        runtime=runtime,
        webhook_token_verifier=AsyncMock(return_value=(True, None)),
    )

    await router.route_webhook(
        message_id="task-1",
        payload=TYPED_HITL_WEBHOOK_PAYLOAD,
        token="token-1",
    )

    assert len(recorded) == 1
    observation = recorded[0]
    assert observation.event_kind == "input_required"
    assert observation.interaction_spec is not None
    assert observation.interaction_spec["interaction_id"] == "travel-planner:abc123"
