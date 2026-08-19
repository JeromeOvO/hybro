from __future__ import annotations

import pytest
from unittest.mock import MagicMock
from langchain_core.messages import AIMessageChunk

from travel_planner_agent.agent import TravelPlannerAgent
from travel_planner_agent.interaction_metadata import (
    HYBRO_A2A_INTERACTION_METADATA_KEY,
    build_input_required_metadata,
)


def test_input_required_metadata_is_exact_typed_and_stable():
    first = build_input_required_metadata(
        "remote-task-1", "Where do you want to go?"
    )
    repeated = build_input_required_metadata(
        "remote-task-1", "Where do you want to go?"
    )
    changed = build_input_required_metadata(
        "remote-task-1", "How many days will you stay?"
    )

    assert first == repeated
    assert first != changed
    assert set(first) == {HYBRO_A2A_INTERACTION_METADATA_KEY}
    spec = first[HYBRO_A2A_INTERACTION_METADATA_KEY]
    assert set(spec) == {"schema_version", "interaction_id", "questions"}
    assert spec["schema_version"] == 1
    assert spec["interaction_id"].startswith("travel-planner:")
    assert spec["questions"] == [
        {
            "question_id": spec["questions"][0]["question_id"],
            "interaction_kind": "questionnaire",
            "prompt": "Where do you want to go?",
            "answer_kind": "text",
            "required": True,
        }
    ]
    assert spec["questions"][0]["question_id"].startswith("travel-details:")


@pytest.mark.asyncio
async def test_tool_call_only_stream_yields_input_required():
    agent = TravelPlannerAgent()
    mock_model = MagicMock()
    mock_with_tools = MagicMock()
    mock_model.bind_tools.return_value = mock_with_tools

    async def fake_astream(messages):
        # Chunk 1: tool call start
        yield AIMessageChunk(
            content="",
            tool_call_chunks=[
                {
                    "name": "AskUserForClarification",
                    "args": '{"question": "Where do ',
                    "id": "call-1",
                    "index": 0,
                }
            ],
        )
        # Chunk 2: tool call args continuation
        yield AIMessageChunk(
            content="",
            tool_call_chunks=[
                {
                    "name": None,
                    "args": 'you want to go?"}',
                    "id": "call-1",
                    "index": 0,
                }
            ],
        )

    mock_with_tools.astream = fake_astream
    agent._model = mock_model

    events = []
    async for event in agent.stream("Plan a trip"):
        events.append(event)

    assert len(events) == 1
    assert events[0] == {
        "content": "Where do you want to go?",
        "done": True,
        "status": "input_required",
    }


@pytest.mark.asyncio
async def test_text_and_tool_call_stream_prefers_input_required():
    agent = TravelPlannerAgent()
    mock_model = MagicMock()
    mock_with_tools = MagicMock()
    mock_model.bind_tools.return_value = mock_with_tools

    async def fake_astream(messages):
        yield AIMessageChunk(content="Thinking about your trip...")
        yield AIMessageChunk(
            content="",
            tool_call_chunks=[
                {
                    "name": "AskUserForClarification",
                    "args": '{"question": "How many days will you stay?"}',
                    "id": "call-2",
                    "index": 0,
                }
            ],
        )

    mock_with_tools.astream = fake_astream
    agent._model = mock_model

    events = []
    async for event in agent.stream("Trip to Tokyo"):
        events.append(event)

    assert len(events) == 2
    assert events[0] == {"content": "Thinking about your trip...", "done": False}
    assert events[1] == {
        "content": "How many days will you stay?",
        "done": True,
        "status": "input_required",
    }


@pytest.mark.asyncio
async def test_text_only_stream_completes_with_done():
    agent = TravelPlannerAgent()
    mock_model = MagicMock()
    mock_with_tools = MagicMock()
    mock_model.bind_tools.return_value = mock_with_tools

    async def fake_astream(messages):
        yield AIMessageChunk(content="Day 1: Arrival in Tokyo. ")
        yield AIMessageChunk(content="Day 2: Explore Shibuya.")

    mock_with_tools.astream = fake_astream
    agent._model = mock_model

    events = []
    async for event in agent.stream("Tokyo for 2 days"):
        events.append(event)

    assert len(events) == 3
    assert events[0] == {"content": "Day 1: Arrival in Tokyo. ", "done": False}
    assert events[1] == {"content": "Day 2: Explore Shibuya.", "done": False}
    assert events[2] == {"content": "", "done": True}


@pytest.mark.asyncio
async def test_tool_call_missing_question_arg_falls_back_safely():
    agent = TravelPlannerAgent()
    mock_model = MagicMock()
    mock_with_tools = MagicMock()
    mock_model.bind_tools.return_value = mock_with_tools

    async def fake_astream(messages):
        yield AIMessageChunk(
            content="",
            tool_call_chunks=[
                {
                    "name": "AskUserForClarification",
                    "args": "{}",
                    "id": "call-3",
                    "index": 0,
                }
            ],
        )

    mock_with_tools.astream = fake_astream
    agent._model = mock_model

    events = []
    async for event in agent.stream("Plan a trip"):
        events.append(event)

    assert len(events) == 1
    assert events[0] == {
        "content": "Could you provide more details?",
        "done": True,
        "status": "input_required",
    }


@pytest.mark.asyncio
async def test_invalid_tool_call_malformed_json_args_falls_back_safely():
    agent = TravelPlannerAgent()
    mock_model = MagicMock()
    mock_with_tools = MagicMock()
    mock_model.bind_tools.return_value = mock_with_tools

    async def fake_astream(messages):
        yield AIMessageChunk(
            content="",
            tool_call_chunks=[
                {
                    "name": "AskUserForClarification",
                    "args": "not-valid-json",
                    "id": "call-4",
                    "index": 0,
                }
            ],
        )

    mock_with_tools.astream = fake_astream
    agent._model = mock_model

    events = []
    async for event in agent.stream("Plan a trip"):
        events.append(event)

    assert len(events) == 1
    assert events[0] == {
        "content": "Could you provide more details?",
        "done": True,
        "status": "input_required",
    }
