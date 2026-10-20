"""C2 step 5: the legacy A2A_RESUME HITL reply path is retired."""

from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

import execution.hitl.adapters as hitl_adapters
import execution.ports as execution_ports
import models.hitl as hitl_models
from common.dto.hitl import HITLApplicationRoute, HITLRouteSnapshot
from execution.hitl.application import HITLApplicationCoordinator
from execution.hitl.factory import create_hitl_service
from execution.hitl.service import HITLService
from execution.hitl.validation import (
    HITLAggregateCorruptionError,
    validate_command_route_consistency,
)


def test_application_route_is_supervisor_only():
    assert [route.value for route in HITLApplicationRoute] == ["supervisor_run"]


def test_a2a_route_snapshot_no_longer_validates():
    with pytest.raises(ValidationError):
        HITLRouteSnapshot.model_validate(
            {
                "route": "a2a_resume",
                "task_id": "task-1",
                "context_id": "context-1",
                "continuation_message_id": "message-1",
                "agent_id": "agent-1",
            }
        )


def test_supervisor_route_snapshot_still_validates():
    snapshot = HITLRouteSnapshot(route="supervisor_run", orchestration_run_id="run-1")

    assert snapshot.route == HITLApplicationRoute.SUPERVISOR_RUN


def test_a2a_resume_command_model_is_gone():
    assert not hasattr(hitl_models, "HITLResumeCommand")
    assert hasattr(hitl_models, "HITLSupervisorEffectCommand")


def test_hitl_service_has_no_agent_reply_path():
    for name in (
        "_handle_agent_response",
        "_project_reconciled_agent_response",
        "agent_reply",
        "continuation",
        "task_notifications",
    ):
        assert not hasattr(HITLService, name), name
    parameters = inspect.signature(HITLService).parameters
    assert "continuation" not in parameters
    assert "task_notifications" not in parameters


@pytest.mark.parametrize("name", ["agent_reply", "continuation", "task_notifications"])
def test_hitl_factory_rejects_retired_ports(name):
    with pytest.raises(TypeError, match=name):
        create_hitl_service(**{name: object()})


def test_application_coordinator_has_no_agent_route():
    assert not hasattr(HITLApplicationCoordinator, "_apply_agent")


def test_retired_adapters_and_ports_are_gone():
    for name in ("A2AHITLContinuationAdapter", "HITLTaskNotificationAdapter"):
        assert not hasattr(hitl_adapters, name), name
    for name in (
        "HITLContinuationPort",
        "HITLAgentReplyPort",
        "HITLTaskNotificationPort",
    ):
        assert not hasattr(execution_ports, name), name


def test_command_validation_rejects_a_non_supervisor_command():
    interaction = {
        "interaction_id": "interaction-1",
        "request_ids": ["request-1"],
    }
    command = {
        "kind": "a2a_resume",
        "interaction_id": "interaction-1",
        "answer_request_ids": ["request-1"],
    }

    with pytest.raises(HITLAggregateCorruptionError):
        validate_command_route_consistency(interaction, command)


def test_container_no_longer_wires_the_agent_reply_adapters():
    import container

    source = inspect.getsource(container)

    assert "A2AHITLContinuationAdapter" not in source
    assert "HITLTaskNotificationAdapter" not in source
