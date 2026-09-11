"""The task history a continuation sees must not repeat the current message.

Default Agents build their LLM history from ``context.current_task`` while the
current user message is passed separately as the query. That split is only
correct if the SDK has not already appended the incoming message to
``task.history`` before calling ``execute``.

This was true under a2a-sdk 0.3, where ``DefaultRequestHandler`` called
``update_with_message`` up front, and the bundled Agents dropped a trailing user
message to compensate. Under a2a-sdk 1.x ``DefaultRequestHandler`` is the V2
handler, which saves the message after the Agent produces an event instead, so
that drop is gone. Nothing pinned the difference, which made it reasonable to
doubt — so this test drives a real handler through a two-turn continuation and
asserts what the Agent actually receives.

If a future SDK upgrade restores the up-front append, this fails here rather than
silently feeding every clarification turn its own question twice.
"""

from __future__ import annotations

import pytest
from a2a.auth.user import UnauthenticatedUser
from a2a.helpers import new_task, new_text_status_update_event
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Message,
    Part,
    Role,
    SendMessageRequest,
    TaskState,
)

CONTEXT_ID = "ctx-1"


class _RecordingExecutor(AgentExecutor):
    """Mimics a bundled Agent: Task first, then a question, pausing for input."""

    def __init__(self) -> None:
        self.turns: list[dict] = []

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        self.turns.append(
            {
                "query": context.get_user_input(),
                "history": [
                    ("".join(p.text for p in m.parts))
                    for m in (task.history if task else [])
                ],
                "status_message": (
                    "".join(p.text for p in task.status.message.parts)
                    if task is not None and task.status.HasField("message")
                    else None
                ),
            }
        )
        if task is None:
            await event_queue.enqueue_event(
                new_task(
                    task_id=context.task_id,
                    context_id=context.context_id,
                    state=TaskState.TASK_STATE_SUBMITTED,
                    history=[context.message],
                )
            )
        await event_queue.enqueue_event(
            new_text_status_update_event(
                task_id=context.task_id,
                context_id=context.context_id,
                state=TaskState.TASK_STATE_INPUT_REQUIRED,
                text=f"question {len(self.turns)}",
            )
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError


def _user_message(text: str, *, task_id: str = "") -> Message:
    message = Message(
        message_id=f"msg-{text}",
        role=Role.ROLE_USER,
        task_id=task_id,
        context_id=CONTEXT_ID,
    )
    part = Part()
    part.text = text
    message.parts.append(part)
    return message


def _handler(executor: AgentExecutor) -> DefaultRequestHandler:
    card = AgentCard(
        name="History Probe",
        version="1.0.0",
        supported_interfaces=[
            AgentInterface(
                url="http://agent.test/",
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            )
        ],
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[AgentSkill(id="s", name="Skill")],
    )
    return DefaultRequestHandler(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )


@pytest.mark.asyncio
async def test_continuation_history_excludes_the_current_message():
    executor = _RecordingExecutor()
    handler = _handler(executor)
    call_context = ServerCallContext(user=UnauthenticatedUser())

    first = await handler.on_message_send(
        SendMessageRequest(message=_user_message("plan a trip")), call_context
    )
    await handler.on_message_send(
        SendMessageRequest(message=_user_message("Oahu", task_id=first.id)),
        call_context,
    )

    opening, continuation = executor.turns

    # The opening turn has no prior task, so it sees no history at all.
    assert opening["history"] == []
    assert opening["query"] == "plan a trip"

    # The continuation's query carries the answer; history carries the earlier
    # turns plus the question that prompted it. The answer must appear once.
    assert continuation["query"] == "Oahu"
    assert continuation["history"] == ["plan a trip"]
    assert continuation["status_message"] == "question 1"
    assert "Oahu" not in continuation["history"]
