import json
import os

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.openai_service import OpenAIService


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def openai_svc():
    svc = object.__new__(OpenAIService)
    svc.client = MagicMock()
    svc.client.chat.completions.create = AsyncMock()
    svc.client.responses.create = AsyncMock()
    return svc


def _chat_completion(content: str):
    """Build mock ChatCompletion for chat.completions.create."""
    return MagicMock(choices=[MagicMock(message=MagicMock(content=content))])


def _responses_result(text: str):
    """Build mock Response for responses.create."""
    return MagicMock(output_text=text)


def _make_base_task(goal: str = "Write a blog post about AI"):
    task = MagicMock()
    part = MagicMock()
    part.root.kind = "text"
    part.root.text = goal
    msg = MagicMock()
    msg.parts = [part]
    task.task.history = [msg]
    return task


def _make_context_data():
    ctx = MagicMock()
    ctx.room_context = ""
    ctx.conversation_history = ""
    ctx.task_context = ""
    return ctx


def _make_agent(agent_id: str, name: str, description: str = "A test agent",
                skills=None, capabilities=None):
    agent = MagicMock()
    agent.agent_id = agent_id
    agent.agent_card.name = name
    agent.agent_card.description = description
    agent.agent_card.skills = skills or []
    agent.agent_card.capabilities = capabilities or {}
    return agent


# ---------------------------------------------------------------------------
# Group 1: expand_query_for_discovery
# ---------------------------------------------------------------------------

class TestExpandQueryForDiscovery:

    @pytest.mark.asyncio
    async def test_returns_expanded_query_string(self, openai_svc):
        openai_svc.client.chat.completions.create.return_value = _chat_completion(
            "expanded query text about AI agents and discovery"
        )

        result = await openai_svc.expand_query_for_discovery("AI")

        assert isinstance(result, str)
        assert "expanded query text" in result
        openai_svc.client.chat.completions.create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_original_on_api_error(self, openai_svc):
        openai_svc.client.chat.completions.create.side_effect = Exception("API down")

        result = await openai_svc.expand_query_for_discovery("AI")

        assert result == "AI"


# ---------------------------------------------------------------------------
# Group 2: decompose_task
# ---------------------------------------------------------------------------

class TestDecomposeTask:

    @pytest.mark.asyncio
    async def test_returns_json_string_with_execution_steps(self, openai_svc):
        valid_json = json.dumps({
            "execution_steps": [
                {
                    "step_number": 1,
                    "step_description": "Research the topic",
                    "execution_context": "Use web search",
                    "expected_output": "Research notes",
                    "depends_on_steps": [],
                },
                {
                    "step_number": 2,
                    "step_description": "Write the blog post",
                    "execution_context": "Based on research",
                    "expected_output": "Blog post draft",
                    "depends_on_steps": [1],
                },
            ]
        })
        openai_svc.client.responses.create.return_value = _responses_result(valid_json)

        with patch.object(openai_svc, "_can_agent_handle_task_alone", new_callable=AsyncMock, return_value="NO"):
            result = await openai_svc.decompose_task(
                _make_base_task(), _make_context_data(), _make_agent("agent-1", "TestAgent")
            )

        parsed = json.loads(result)
        assert "execution_steps" in parsed
        assert len(parsed["execution_steps"]) == 2
        openai_svc.client.responses.create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_fallback_json_string_on_malformed_response(self, openai_svc):
        openai_svc.client.responses.create.return_value = _responses_result(
            "This is not JSON at all, just plain garbage text with no braces"
        )

        with patch.object(openai_svc, "_can_agent_handle_task_alone", new_callable=AsyncMock, return_value="NO"):
            result = await openai_svc.decompose_task(
                _make_base_task(), _make_context_data(), _make_agent("agent-1", "TestAgent")
            )

        parsed = json.loads(result)
        assert "execution_steps" in parsed
        assert len(parsed["execution_steps"]) == 1

    @pytest.mark.asyncio
    async def test_single_agent_shortcircuit_skips_llm(self, openai_svc):
        best_agent = _make_agent("agent-1", "TestAgent")

        with patch.object(openai_svc, "_can_agent_handle_task_alone", new_callable=AsyncMock, return_value="YES"):
            result = await openai_svc.decompose_task(
                _make_base_task(), _make_context_data(), best_agent
            )

        parsed = json.loads(result)
        assert "execution_steps" in parsed
        assert len(parsed["execution_steps"]) == 1
        assert "TestAgent" in parsed["execution_steps"][0]["execution_context"]
        openai_svc.client.responses.create.assert_not_awaited()


# ---------------------------------------------------------------------------
# Group 3: call_supervisor_llm_json
# ---------------------------------------------------------------------------

class TestCallSupervisorLlmJson:

    @pytest.mark.asyncio
    async def test_returns_parsed_dict_from_json_response(self, openai_svc):
        openai_svc.client.chat.completions.create.return_value = _chat_completion(
            '{"action": "proceed", "confidence": 0.95}'
        )

        result = await openai_svc.call_supervisor_llm_json("system", "user")

        assert isinstance(result, dict)
        assert result == {"action": "proceed", "confidence": 0.95}

    @pytest.mark.asyncio
    async def test_raises_value_error_on_empty_content(self, openai_svc):
        openai_svc.client.chat.completions.create.return_value = _chat_completion(None)

        with pytest.raises(ValueError, match="Empty response from Supervisor LLM"):
            await openai_svc.call_supervisor_llm_json("system", "user")

    @pytest.mark.asyncio
    async def test_raises_json_decode_error_on_invalid_json(self, openai_svc):
        openai_svc.client.chat.completions.create.return_value = _chat_completion(
            "not json at all"
        )

        with pytest.raises(json.JSONDecodeError):
            await openai_svc.call_supervisor_llm_json("system", "user")


# ---------------------------------------------------------------------------
# Group 4: analyze_message_routing
# ---------------------------------------------------------------------------

class TestAnalyzeMessageRouting:

    @pytest.mark.asyncio
    async def test_returns_routing_dict_with_strategy_and_agents(self, openai_svc):
        routing_json = json.dumps({
            "strategy": "parallel",
            "agent_ids": ["agent-1", "agent-2"],
            "agent_reasons": {
                "agent-1": "Expert in research",
                "agent-2": "Expert in writing",
            },
            "reasoning": "Task benefits from parallel expertise",
            "needs_debate": True,
        })
        openai_svc.client.chat.completions.create.return_value = _chat_completion(routing_json)

        agents = [
            _make_agent("agent-1", "Agent One", "First agent"),
            _make_agent("agent-2", "Agent Two", "Second agent"),
        ]
        result = await openai_svc.analyze_message_routing("Compare Python and Rust", agents)

        assert isinstance(result, dict)
        assert result["strategy"] == "parallel"
        assert result["agent_ids"] == ["agent-1", "agent-2"]
        assert "agent-1" in result["agent_reasons"]
        assert "reasoning" in result
        assert result["needs_debate"] is True

    @pytest.mark.asyncio
    async def test_error_returns_fallback_with_first_agent(self, openai_svc):
        openai_svc.client.chat.completions.create.side_effect = Exception("timeout")

        agents = [
            _make_agent("agent-1", "Agent One", "First agent"),
            _make_agent("agent-2", "Agent Two", "Second agent"),
        ]
        result = await openai_svc.analyze_message_routing("some query", agents)

        assert result["strategy"] == "single"
        assert result["agent_ids"] == ["agent-1"]
        assert result["needs_debate"] is False


# ---------------------------------------------------------------------------
# Group 5: select_best_agent_for_task
# ---------------------------------------------------------------------------

class TestSelectBestAgentForTask:

    @staticmethod
    def _make_selectable_agent(agent_id, name, description="An agent"):
        agent = MagicMock()
        agent.agent_id = agent_id
        agent.agent_card.name = name
        agent.agent_card.description = description
        agent.agent_card.capabilities = {"streaming": True}
        skill = MagicMock()
        skill.name = "skill1"
        skill.id = "s1"
        agent.agent_card.skills = [skill]
        return agent

    @pytest.mark.asyncio
    async def test_returns_matching_agent_id(self, openai_svc):
        openai_svc.client.chat.completions.create.return_value = _chat_completion(
            "The best agent for this task is agent-2 because of its writing skills."
        )

        agents = [
            self._make_selectable_agent("agent-1", "Agent One"),
            self._make_selectable_agent("agent-2", "Agent Two"),
        ]
        result = await openai_svc.select_best_agent_for_task("Write a blog post", agents)

        assert result == "agent-2"

    @pytest.mark.asyncio
    async def test_falls_back_to_first_agent_on_no_match(self, openai_svc):
        openai_svc.client.chat.completions.create.return_value = _chat_completion(
            "unknown-agent-999 would be ideal."
        )

        agents = [
            self._make_selectable_agent("agent-1", "Agent One"),
            self._make_selectable_agent("agent-2", "Agent Two"),
        ]
        result = await openai_svc.select_best_agent_for_task("Write a blog post", agents)

        assert result == "agent-1"
