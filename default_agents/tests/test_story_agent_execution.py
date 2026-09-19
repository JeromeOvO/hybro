"""Story model failures must terminate tasks without publishing story artifacts."""

import asyncio
import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

from a2a.helpers import new_text_message
from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.types import (
    Role,
    SendMessageRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatusUpdateEvent,
)
from story_agent import agent as story_agent

# Bundled executors import their adjacent agent as a top-level module at runtime.
# Keep this import isolated from other agents and the backend's agent package.
with patch.dict(sys.modules, {"agent": story_agent}):
    from story_agent.agent_executor import StoryAgentExecutor


class RecordingQueue(EventQueue):
    def __init__(self):
        self.events = []

    async def enqueue_event(self, event):
        self.events.append(event)


def request_context():
    return RequestContext(
        call_context=ServerCallContext(),
        request=SendMessageRequest(
            message=new_text_message("Tell a story", role=Role.ROLE_USER)
        ),
    )


class StoryAgentExecutionTests(unittest.IsolatedAsyncioTestCase):
    def assert_failed_without_story(self, queue, secret, partial=""):
        self.assertEqual(
            [type(event) for event in queue.events], [Task, TaskStatusUpdateEvent]
        )
        task, terminal = queue.events
        self.assertEqual(terminal.task_id, task.id)
        self.assertEqual(terminal.context_id, task.context_id)
        self.assertEqual(terminal.status.state, TaskState.TASK_STATE_FAILED)
        output = "\n".join(str(event) for event in queue.events)
        self.assertNotIn(secret, output)
        if partial:
            self.assertNotIn(partial, output)

    async def test_model_setup_failure_is_safe_failed_task(self):
        secret = "fixture-setup-secret"
        executor = StoryAgentExecutor()
        queue = RecordingQueue()
        output = io.StringIO()
        config = SimpleNamespace(
            text_model="fixture-model", base_url="https://invalid.example", token=secret
        )
        with (
            patch.object(story_agent, "get_config", return_value=config),
            patch.object(story_agent, "ChatOpenAI", side_effect=ValueError(secret)),
            redirect_stdout(output),
            redirect_stderr(output),
        ):
            await executor.execute(request_context(), queue)

        self.assert_failed_without_story(queue, secret)
        self.assertNotIn(secret, output.getvalue())

    async def test_stream_failure_discards_even_partial_story(self):
        secret = "fixture-stream-secret"
        for partial in ("", "An unfinished secret draft"):
            with self.subTest(after_partial=bool(partial)):

                async def stream(_messages, partial=partial):
                    if partial:
                        yield SimpleNamespace(content=partial)
                    raise RuntimeError(secret)

                executor = StoryAgentExecutor()
                queue = RecordingQueue()
                output = io.StringIO()
                model = Mock(astream=stream)
                with (
                    patch.object(executor.agent, "_ensure_model", return_value=model),
                    redirect_stdout(output),
                    redirect_stderr(output),
                ):
                    await executor.execute(request_context(), queue)

                self.assert_failed_without_story(queue, secret, partial)
                self.assertNotIn(secret, output.getvalue())

    async def test_success_preserves_story_even_when_prose_mentions_errors(self):
        chunks = [
            '"Sorry, an error occurred while processing your request," ',
            "said the robot. Then it told a wonderful story.",
        ]

        async def stream(_messages):
            for content in chunks:
                yield SimpleNamespace(content=content)

        executor = StoryAgentExecutor()
        queue = RecordingQueue()
        with patch.object(
            executor.agent, "_ensure_model", return_value=Mock(astream=stream)
        ):
            await executor.execute(request_context(), queue)

        self.assertEqual(
            [type(event) for event in queue.events],
            [Task, TaskArtifactUpdateEvent, TaskStatusUpdateEvent],
        )
        task, artifact, terminal = queue.events
        expected = "".join(chunks)
        self.assertEqual(artifact.task_id, task.id)
        self.assertEqual(artifact.context_id, task.context_id)
        self.assertEqual(artifact.artifact.parts[0].text, expected)
        self.assertTrue(artifact.last_chunk)
        self.assertEqual(terminal.status.state, TaskState.TASK_STATE_COMPLETED)
        self.assertEqual(terminal.status.message.parts[0].text, expected)

    async def test_cancellation_propagates_without_publishing_partial_story(self):
        async def stream(_messages):
            yield SimpleNamespace(content="An unfinished draft")
            raise asyncio.CancelledError()

        executor = StoryAgentExecutor()
        queue = RecordingQueue()
        with patch.object(
            executor.agent, "_ensure_model", return_value=Mock(astream=stream)
        ):
            with self.assertRaises(asyncio.CancelledError):
                await executor.execute(request_context(), queue)

        self.assertEqual([type(event) for event in queue.events], [Task])
