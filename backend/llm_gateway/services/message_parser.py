import json
import re
from typing import Any

from common.dto import ParsedUserMessageRequest
from common.protocols import LLMStructuredGateway


class MessageParserLLMService:
    def __init__(
        self,
        llm_provider: LLMStructuredGateway,
        default_model: str = "classifier_ai_model",
    ) -> None:
        self._llm_provider = llm_provider
        self._default_model = default_model

    async def parse_user_message(self, request: ParsedUserMessageRequest) -> dict:
        response = await self._llm_provider.generate_structured(
            _parse_messages(request),
            schema=None,
            json_mode=True,
            model=self._default_model,
        )
        data = response if isinstance(response, dict) else response.data
        return _normalize_parse_result(data, request)


def _parse_messages(request: ParsedUserMessageRequest) -> list[dict[str, str]]:
    agent_list = "\n".join(
        (
            f"- Agent ID: {agent.agent_id}\n"
            f"  Name: {agent.name}\n"
            f"  Description: {agent.description or 'No description'}\n"
            f"  Capabilities: {json.dumps(agent.capabilities)}\n"
            f"  Skills: {', '.join(agent.skills)}"
        )
        for agent in request.agents
    )
    explicit_mentions = "\n".join(
        f"- {mention.agent_name} ({mention.agent_id})"
        for mention in request.explicit_mentions
    )
    if request.auto_assign_agents and request.selected_agents:
        system_prompt = (
            "You are an expert task analyzer and router for a multi-agent "
            "collaboration system.\n\n"
            "Process:\n"
            "1. Decide whether the message is simple or genuinely complex.\n"
            "2. Simple messages include questions, greetings, follow-ups, "
            "single requests, and conversational messages referencing prior "
            "context. When in doubt, do not decompose.\n"
            "3. For complex tasks, break work into logical sub-tasks with "
            "clear dependencies.\n\n"
            "Agent assignment rules for AUTO MODE:\n"
            "- Assign every task step to one available agent.\n"
            "- Use only agents from the selected/available agent pool.\n"
            "- Match agent descriptions, capabilities, and skills to each step.\n"
            "- If only one agent is available, assign it to every step.\n"
            "- If the message mentions a specific agent with <@...>, prioritize "
            "that agent for relevant steps.\n"
            "- If unsure, assign the first available agent.\n"
            "- If needs_decomposition is false, return exactly one task step.\n\n"
            "Execution focus:\n"
            "- task_content must be a direct action that produces a concrete "
            "deliverable.\n"
            "- Use verbs like Create, Write, Generate, Build, Implement.\n"
            "- Avoid planning-only tasks such as Plan how to, Outline, or "
            "Analyze requirements for.\n"
            "- Remove all <@...> mentions from task_content.\n\n"
            "Return valid JSON only with this shape: message_type, original_text, "
            "needs_decomposition, decomposition_reason, task_steps. Each task "
            "step has step_id, agent_id, agent_name, task_content, dependencies."
        )
    else:
        system_prompt = (
            "You are an expert task analyzer and decomposer for a multi-agent "
            "collaboration system.\n\n"
            "Process:\n"
            "1. Decide whether the message is simple or genuinely complex.\n"
            "2. If simple, keep it as one step.\n"
            "3. If complex, break it into logical sub-tasks with clear "
            "dependencies.\n\n"
            "Agent assignment rules for CURATED MODE:\n"
            "- Only assign agents that are explicitly mentioned in the message "
            "using <@agent-id|agent-name> format.\n"
            "- If there are no mentions in the original message, every task "
            "step must have agent_id null and agent_name null.\n"
            "- If agents are mentioned, use only those mentioned agents.\n"
            "- Extract agent_id and agent_name from the mention format.\n"
            "- Do not auto-assign agents based on task type, capabilities, or "
            "available room membership when no mention is present.\n"
            "- If needs_decomposition is false, return exactly one task step "
            "whose task_content is the original text with all mentions removed.\n\n"
            "Execution focus:\n"
            "- task_content must be a direct action that produces a concrete "
            "deliverable.\n"
            "- Use verbs like Create, Write, Generate, Build, Implement.\n"
            "- Avoid planning-only tasks such as Plan how to, Outline, or "
            "Analyze requirements for.\n\n"
            "Return valid JSON only with this shape: message_type, original_text, "
            "needs_decomposition, decomposition_reason, task_steps. Each task "
            "step has step_id, agent_id, agent_name, task_content, dependencies."
        )
    user_prompt = (
        f"Message: {request.message_text}\n\n"
        f"Selected agents: {json.dumps(request.selected_agents)}\n\n"
        f"Explicit mentions:\n{explicit_mentions or 'None'}\n\n"
        f"Available agent details:\n{agent_list or 'No agent details'}\n\n"
        f"Conversation context:\n{request.conversation_context or 'None'}\n\n"
        f"Auto assign agents: {request.auto_assign_agents}"
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _normalize_parse_result(data: Any, request: ParsedUserMessageRequest) -> dict:
    if not isinstance(data, dict):
        raise TypeError("LLM parser response must be a JSON object")

    result = dict(data)
    task_steps = result.get("task_steps")
    if not isinstance(task_steps, list):
        task_steps = []

    mention_names = _mentioned_agent_names(request)
    selected_agents = request.selected_agents
    normalized_steps: list[dict[str, Any]] = []
    for index, raw_step in enumerate(task_steps, start=1):
        step = dict(raw_step) if isinstance(raw_step, dict) else {}
        step.setdefault("step_id", f"step_{index}")
        step.setdefault("dependencies", [])
        if not isinstance(step.get("dependencies"), list):
            step["dependencies"] = []
        if isinstance(step.get("task_content"), str):
            step["task_content"] = _strip_mentions(step["task_content"])

        _normalize_step_agent(step, request, selected_agents, mention_names)
        normalized_steps.append(step)

    if request.auto_assign_agents and selected_agents and not normalized_steps:
        fallback_id = next(iter(selected_agents))
        normalized_steps.append(
            {
                "step_id": "step_1",
                "agent_id": fallback_id,
                "agent_name": selected_agents[fallback_id],
                "task_content": _strip_mentions(request.message_text),
                "dependencies": [],
            }
        )

    result["task_steps"] = normalized_steps
    return result


def _normalize_step_agent(
    step: dict[str, Any],
    request: ParsedUserMessageRequest,
    selected_agents: dict[str, str],
    mention_names: dict[str, str],
) -> None:
    agent_id = step.get("agent_id")
    if not isinstance(agent_id, str) or not agent_id:
        _clear_step_agent(step)
        return

    if request.auto_assign_agents and selected_agents:
        if agent_id not in selected_agents:
            fallback_id = next(iter(selected_agents))
            step["agent_id"] = fallback_id
            step["agent_name"] = selected_agents[fallback_id]
        else:
            step["agent_name"] = selected_agents[agent_id]
        return

    if agent_id in selected_agents and agent_id in mention_names:
        step["agent_name"] = mention_names[agent_id]
    else:
        _clear_step_agent(step)


def _clear_step_agent(step: dict[str, Any]) -> None:
    step["agent_id"] = None
    step["agent_name"] = None


def _mentioned_agent_names(request: ParsedUserMessageRequest) -> dict[str, str]:
    names = {
        mention.agent_id: mention.agent_name
        for mention in request.explicit_mentions
        if mention.agent_id
    }
    for agent_id, agent_name in re.findall(
        r"<@([^|]+)\|([^>]+)>", request.message_text
    ):
        cleaned_id = agent_id.strip()
        names[cleaned_id] = request.selected_agents.get(cleaned_id, agent_name.strip())
    return names


def _strip_mentions(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<@[^>]+>", "", text)).strip()


__all__ = ["MessageParserLLMService"]
