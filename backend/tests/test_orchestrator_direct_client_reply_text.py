"""An agent's reply text reaches the orchestrator once."""

from __future__ import annotations

from a2a_adapter.orchestrator_direct_client import _task_to_observation_kwargs
from common.types import (
    Artifact,
    Message,
    MessageRole,
    Part,
    Task,
    TaskStatus,
    TextPart,
)


def completed_task(status_text: str | None, artifact_text: str) -> Task:
    message = (
        Message(role=MessageRole.AGENT, parts=[Part(root=TextPart(text=status_text))])
        if status_text is not None
        else None
    )
    return Task(
        id="task-1",
        context_id="ctx-1",
        status=TaskStatus(state="completed", message=message),
        artifacts=[
            Artifact(
                artifact_id="art-1",
                name="current_result",
                parts=[Part(root=TextPart(text=artifact_text))],
            )
        ],
    )


def reply_text(task: Task) -> str:
    kwargs = _task_to_observation_kwargs(
        task,
        source_kind="direct",
        call_record_id="call-1",
        binding_scope="scope",
        agent_id="travel",
        task_id="task-1",
        context_id="ctx-1",
    )
    return kwargs["content"][0]["text"]


def test_a_status_message_repeating_the_artifact_is_kept_once():
    plan = "Day 1: Louvre\nDay 2: Versailles"
    assert reply_text(completed_task(plan, plan)) == plan
    assert reply_text(completed_task(f"  {plan}\n", plan)) == plan


def test_different_status_and_artifact_text_are_both_kept():
    assert reply_text(completed_task("Here is your plan.", "Day 1: Louvre")) == (
        "Here is your plan.\nDay 1: Louvre"
    )


def test_artifact_text_alone_is_the_reply():
    assert reply_text(completed_task(None, "Day 1: Louvre")) == "Day 1: Louvre"
