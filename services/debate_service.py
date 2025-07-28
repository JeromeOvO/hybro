import asyncio
import uuid
from datetime import datetime
from typing import Any

from a2a.types import AgentCard, Message, TaskSendParams, TextPart

from common.types import TaskSendParams
from common.utils.remote_agent_connection import RemoteAgentConnections
from services.agent_service import agent_service
from services.openai_service import openai_service


class DebateService:
    def __init__(self):
        self.openai_service = openai_service
        self.agent_service = agent_service
        self.active_debates = {}  # Store active debate sessions

    async def conduct_debate(
        self, original_user_input: str, agent_ids: list[str], rounds: int = 3
    ) -> dict[str, Any]:
        """
        Conduct a multi-round debate between agents on a given topic.

        Args:
            original_user_input: The original question/topic to debate
            agent_ids: List of agent IDs to participate in the debate
            rounds: Number of debate rounds to conduct

        Returns:
            Dict containing:
            - final_answer: The concluded answer from the debate
            - confirmation_count: Number of agents that confirm the final conclusion
            - debate_history: Full history of the debate
            - participating_agents: List of agent details
        """
        debate_id = str(uuid.uuid4())

        try:
            # Initialize debate session
            debate_session = await self._initialize_debate_session(
                debate_id, original_user_input, agent_ids
            )

            # Conduct multiple rounds of debate
            for round_num in range(1, rounds + 1):
                print(f"Starting debate round {round_num}/{rounds}")
                await self._conduct_debate_round(debate_session, round_num)

            # Generate final conclusion using OpenAI
            final_answer = await self._generate_final_conclusion(debate_session)

            # Get confirmation from agents about the final conclusion
            confirmation_count = await self._get_agent_confirmations(
                debate_session, final_answer
            )

            # Clean up active debate
            if debate_id in self.active_debates:
                del self.active_debates[debate_id]

            return {
                "final_answer": final_answer,
                "confirmation_count": confirmation_count,
                "total_agents": len(agent_ids),
                "debate_history": debate_session["history"],
                "participating_agents": debate_session["agents"],
            }

        except Exception as e:
            print(f"Error in debate conduct: {str(e)}")
            # Clean up on error
            if debate_id in self.active_debates:
                del self.active_debates[debate_id]
            raise

    async def _initialize_debate_session(
        self, debate_id: str, user_input: str, agent_ids: list[str]
    ) -> dict[str, Any]:
        """Initialize a debate session with participating agents."""

        # Get agent details
        agents = []
        for agent_id in agent_ids:
            agent = await self.agent_service.get_agent(agent_id)
            if agent:
                agents.append(
                    {
                        "agent_id": agent_id,
                        "agent_card": agent.agentCard,
                        "responses": [],
                    }
                )

        if not agents:
            raise ValueError("No valid agents found for debate")

        debate_session = {
            "debate_id": debate_id,
            "original_input": user_input,
            "agents": agents,
            "history": [],
            "current_round": 0,
            "created_at": datetime.now(),
        }

        self.active_debates[debate_id] = debate_session
        return debate_session

    async def _conduct_debate_round(
        self, debate_session: dict[str, Any], round_num: int
    ):
        """Conduct a single round of debate."""

        debate_session["current_round"] = round_num
        round_responses = []

        # Get responses from all agents in parallel
        tasks = []
        for agent_info in debate_session["agents"]:
            task = self._get_agent_response(
                agent_info,
                debate_session["original_input"],
                debate_session["history"],
                round_num,
            )
            tasks.append(task)

        # Wait for all agent responses
        responses = await asyncio.gather(*tasks, return_exceptions=True)

        # Process responses
        for i, response in enumerate(responses):
            agent_info = debate_session["agents"][i]

            if isinstance(response, Exception):
                print(
                    f"Error getting response from agent {agent_info['agent_id']}: {response}"
                )
                response_text = (
                    f"Agent {agent_info['agent_id']} failed to respond: {str(response)}"
                )
            else:
                response_text = response

            round_entry = {
                "round": round_num,
                "agent_id": agent_info["agent_id"],
                "response": response_text,
                "timestamp": datetime.now(),
            }

            agent_info["responses"].append(response_text)
            round_responses.append(round_entry)

        debate_session["history"].extend(round_responses)

    async def _get_agent_response(
        self,
        agent_info: dict[str, Any],
        original_input: str,
        debate_history: list[dict[str, Any]],
        round_num: int,
    ) -> str:
        """Get response from a single agent."""

        try:
            agent_card = AgentCard(**agent_info["agent_card"])

            # Prepare context from previous rounds
            context = self._prepare_debate_context(
                debate_history, agent_info["agent_id"]
            )

            # Create the debate prompt
            if round_num == 1:
                prompt = (
                    f"Please provide your analysis and opinion on: {original_input}"
                )
            else:
                prompt = f"""Original question: {original_input}

Previous debate responses from other agents:
{context}

Based on the discussion so far, please provide your updated analysis and opinion. You can agree, disagree, or build upon previous points."""

            # Check if agent is remote or use OpenAI
            if agent_info.get("is_remote", False):
                # Send to remote agent
                client = RemoteAgentConnections(agent_card)
                payload = TaskSendParams(
                    id=str(uuid.uuid4()),
                    sessionId=str(uuid.uuid4()),
                    message=Message(role="user", parts=[TextPart(text=prompt)]),
                    acceptedOutputModes=["text"],
                    metadata={},
                )

                task_result = await client.send_task(payload)
                return self._extract_text_from_task(task_result)
            else:
                # Use OpenAI for local agents or fallback
                if round_num == 1:
                    return await self.openai_service.short_debate_with_openai(
                        original_input, ""
                    )
                else:
                    return await self.openai_service.long_debate_with_openai(
                        original_input, context
                    )

        except Exception as e:
            print(f"Error getting response from agent {agent_info['agent_id']}: {e}")
            return f"Error: Agent {agent_info['agent_id']} failed to respond - {str(e)}"

    def _prepare_debate_context(
        self, debate_history: list[dict[str, Any]], current_agent_id: str
    ) -> str:
        """Prepare context from debate history for an agent."""

        context_parts = []
        for entry in debate_history:
            if (
                entry["agent_id"] != current_agent_id
            ):  # Exclude current agent's own responses
                context_parts.append(f"Agent {entry['agent_id']}: {entry['response']}")

        return "\n\n".join(context_parts)

    async def _generate_final_conclusion(self, debate_session: dict[str, Any]) -> str:
        """Generate final conclusion from all debate responses."""

        # Collect all responses
        all_responses = []
        for entry in debate_session["history"]:
            all_responses.append(entry["response"])

        # Use OpenAI to summarize the debate
        final_answer = await self.openai_service.summarize_debate_answer(all_responses)

        return final_answer

    async def _get_agent_confirmations(
        self, debate_session: dict[str, Any], final_answer: str
    ) -> int:
        """Get confirmations from agents about the final conclusion."""

        confirmation_tasks = []
        for agent_info in debate_session["agents"]:
            task = self._get_agent_confirmation(
                agent_info, debate_session["original_input"], final_answer
            )
            confirmation_tasks.append(task)

        confirmations = await asyncio.gather(
            *confirmation_tasks, return_exceptions=True
        )

        # Count positive confirmations
        confirmation_count = 0
        for i, confirmation in enumerate(confirmations):
            if isinstance(confirmation, Exception):
                print(
                    f"Error getting confirmation from agent {debate_session['agents'][i]['agent_id']}: {confirmation}"
                )
                continue

            # Simple confirmation check - look for positive keywords
            if self._is_positive_confirmation(confirmation):
                confirmation_count += 1

        return confirmation_count

    async def _get_agent_confirmation(
        self, agent_info: dict[str, Any], original_input: str, final_answer: str
    ) -> str:
        """Get confirmation from a single agent about the final conclusion."""

        try:
            prompt = f"""Original question: {original_input}

Final conclusion from the debate: {final_answer}

Do you agree with this final conclusion? Please respond with "Yes, I agree" or "No, I disagree" followed by a brief explanation."""

            # Use OpenAI for confirmation (simpler approach)
            response = await self.openai_service.short_debate_with_openai(
                f"Confirmation request: {prompt}", ""
            )

            return response

        except Exception as e:
            print(
                f"Error getting confirmation from agent {agent_info['agent_id']}: {e}"
            )
            return f"Error: Could not get confirmation - {str(e)}"

    def _is_positive_confirmation(self, response: str) -> bool:
        """Check if a response indicates positive confirmation."""

        response_lower = response.lower()
        positive_indicators = [
            "yes",
            "agree",
            "correct",
            "accurate",
            "right",
            "confirm",
            "support",
            "endorse",
            "accept",
        ]
        negative_indicators = [
            "no",
            "disagree",
            "incorrect",
            "wrong",
            "reject",
            "oppose",
            "deny",
            "refuse",
        ]

        # Count positive and negative indicators
        positive_count = sum(
            1 for indicator in positive_indicators if indicator in response_lower
        )
        negative_count = sum(
            1 for indicator in negative_indicators if indicator in response_lower
        )

        return positive_count > negative_count

    def _extract_text_from_task(self, task_obj) -> str:
        """Extract text from task response (similar to HostAgent implementation)."""

        # Try artifacts first
        if getattr(task_obj, "artifacts", None):
            txt = "".join(
                p.text for p in task_obj.artifacts[0].parts if isinstance(p, TextPart)
            )
            if txt:
                return txt

        # Try output.parts
        if getattr(task_obj, "output", None) and task_obj.output.parts:
            txt = "".join(
                p.text for p in task_obj.output.parts if isinstance(p, TextPart)
            )
            if txt:
                return txt

        # Try last agent message
        if getattr(task_obj, "messages", None):
            for msg in reversed(task_obj.messages):
                if msg.role == "agent":
                    txt = "".join(p.text for p in msg.parts if isinstance(p, TextPart))
                    if txt:
                        return txt

        return "No response text found"


# Create service instance
debate_service = DebateService()
