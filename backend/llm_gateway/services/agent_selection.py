import json

from common.dto import AgentRoutingCandidate
from common.protocols import LLMTextGateway
from llm_gateway.errors import LLMModelRoutingError


class AgentSelectionLLMService:
    def __init__(
        self,
        llm_provider: LLMTextGateway,
        default_model: str = "classifier_ai_model",
    ) -> None:
        self._llm_provider = llm_provider
        self._default_model = default_model

    async def select_best_agent_for_task(
        self,
        meta_task_description: str,
        agents: list[AgentRoutingCandidate],
    ) -> str:
        if not agents:
            raise ValueError("No agents available for selection")

        system_prompt = (
            "You are an AI tasked with selecting the most appropriate agent for "
            "a given task. Analyze the task description and candidate agent "
            "capabilities, then return only the ID of the best matching agent."
        )
        agents_text = "\n\n".join(
            _format_candidate(index, agent)
            for index, agent in enumerate(agents, start=1)
        )
        prompt = (
            f"Task Description: {meta_task_description}\n\n"
            f"Available Agents:\n{agents_text}\n\n"
            "Which agent ID is most appropriate for this task?"
        )
        try:
            response = await self._llm_provider.generate(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                model=self._default_model,
            )
            content = response.content.strip()
            for agent in agents:
                if agent.agent_id in content:
                    return agent.agent_id
        except LLMModelRoutingError:
            raise
        except Exception:
            pass
        return agents[0].agent_id

    async def rank_agents_for_task(
        self,
        meta_task_description: str,
        agents: list[AgentRoutingCandidate],
    ) -> list[str]:
        """Return a safe candidate-ID permutation, or lexical order on failure."""
        if not agents:
            return []
        lexical_order = [agent.agent_id for agent in agents]
        system_prompt = (
            "Rank the candidate agents for the task. Return only a JSON array "
            "containing candidate IDs, best first. Do not invent IDs."
        )
        agents_text = "\n\n".join(
            _format_candidate(index, agent)
            for index, agent in enumerate(agents, start=1)
        )
        prompt = (
            f"Task Description: {meta_task_description}\n\n"
            f"Available Agents:\n{agents_text}\n\n"
            "Return the ranked candidate ID array."
        )
        try:
            response = await self._llm_provider.generate(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                model=self._default_model,
            )
            parsed = json.loads(response.content.strip())
            if not isinstance(parsed, list):
                return lexical_order
            allowed = set(lexical_order)
            ranked: list[str] = []
            for value in parsed:
                candidate_id = str(value)
                if candidate_id in allowed and candidate_id not in ranked:
                    ranked.append(candidate_id)
            return [*ranked, *(item for item in lexical_order if item not in ranked)]
        except Exception:
            return lexical_order


def _format_candidate(index: int, agent: AgentRoutingCandidate) -> str:
    capabilities = [f"{key}: {value}" for key, value in agent.capabilities.items()]
    return (
        f"Agent {index}:\n"
        f"ID: {agent.agent_id}\n"
        f"Name: {agent.name}\n"
        f"Description: {agent.description}\n"
        f"Capabilities: {', '.join(capabilities)}\n"
        f"Skills: {', '.join(agent.skills)}"
    )


__all__ = ["AgentSelectionLLMService"]
