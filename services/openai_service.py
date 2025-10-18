import json
import os
import re
from config.settings import settings
from a2a.types import Role
from dotenv import load_dotenv
from openai import AsyncOpenAI
from openai.types.chat import (
    ChatCompletionSystemMessageParam,
    ChatCompletionUserMessageParam,
)

from common.utils.logger import get_logger
from models.agent import Agent
from models.memory import ContextData, MemoryContent
from models.room import RoomAgentMessage
from models.task import BaseTask

load_dotenv()

logger = get_logger(__name__)


class OpenAIService:
    def __init__(self):
        self.client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    async def get_embedding(
        self, text: str, target_dim: int | None = None
    ) -> list[float]:
        """Get embedding for text

        Args:
            text: The text to embed
            target_dim: Optional target dimension to resize the embedding to

        Returns:
            List of embedding values (original or resized)
        """
        response = await self.client.embeddings.create(
            input=text,
            model=os.getenv("EMBEDDING_MODEL") or "text-embedding-3-small",
        )

        embedding = response.data[0].embedding

        return embedding

    async def decompose_task(
        self, base_task: BaseTask, context_data: ContextData, best_agent: Agent | None
    ) -> str:
        """
        Decompose a base task into subtasks using OpenAI.
        Returns a JSON string with structured execution steps.
        """
        # Extract task goal
        if (
            base_task.task.history
            and len(base_task.task.history) > 0
            and len(base_task.task.history[0].parts) > 0
        ):
            first_part = base_task.task.history[0].parts[0].root
            task_goal = (
                first_part.text
                if first_part.kind == "text"
                else "No text content available"
            )
        else:
            task_goal = "No task goal provided"

        # Check if a single agent can handle the task before decomposition
        try:
            # Use LLM to determine if the best matched agent can handle the task alone
            can_handle_alone = await self._can_agent_handle_task_alone(
                task_goal, best_agent
            )

            if can_handle_alone == "YES":
                # Return the original task as a single subtask
                return json.dumps(
                    {
                        "execution_steps": [
                            {
                                "step_number": 1,
                                "step_description": task_goal,
                                "execution_context": f"Task assigned to agent: {best_agent.agent_card.name}",
                                "expected_output": "Complete task resolution",
                                "depends_on_steps": [],
                            }
                        ]
                    }
                )
        except Exception as e:
            logger.warning(f"Error checking single agent capability: {str(e)}")
            # Continue with normal decomposition if agent check fails

        # Continue with normal decomposition logic
        system_prompt = """You are a task decomposition assistant. Your job is
        to break down tasks into subtasks using a hybrid strategy that combines:
        1.	Recursive Least Commitment Decomposition (RLCD):
        - Decompose tasks incrementally.
        - Avoid overcommitting to specific methods too early.
        - Keep steps abstract until details are necessary.
        2.	Constraint-Based Decomposition (CBD):
        - Apply explicit constraints from the task description (e.g., deadlines, resources, ordering rules).
        - Use these constraints to validate or eliminate candidate subtasks.
        3.	Merge-and-Prune:
        - Merge overlapping or redundant subtasks.
        - Prune infeasible, irrelevant, or duplicate branches. 
        Important: Max 8 steps.
        Your goal is to create a structured execution plan for all steps.
        
        Return the response in the following JSON format:
        {
            "execution_steps": [
                {
                    "step_number": 1,
                    "step_description": "Concise description of what this step does",
                    "execution_context": "Context needed for the step",
                    "expected_output": "What this step should produce",
                    "depends_on_steps": []  # List of step numbers this step depends on
                }
            ]
        }
        
        Each step should be:
        1. Specific and actionable
        2. Clear enough for an AI agent to understand and solve
        3. Independent or minimal dependencies on other steps
        4. If a step needs results from previous steps, list those step numbers in depends_on_steps
        """

        prompt = f"""Task Goal: {task_goal}
        Chat Context: {context_data}

Please decompose this task into a structured execution plan with all necessary steps.

IMPORTANT: Do not include any other text in your response. Only return the JSON object.
"""

        messages = [
            ChatCompletionSystemMessageParam(role="system", content=system_prompt),
            ChatCompletionUserMessageParam(role="user", content=prompt),
        ]

        try:
            # response = await self.client.chat.completions.create(
            #     model=os.getenv("LEAD_AI_MODEL") or "gpt-4o-mini",
            #     messages=messages,
            #     temperature=0.3,  # Lower temperature for more consistent structured output
            #     max_tokens=int(
            #         os.getenv("SUBTASK_MAX_TOKENS", "4096")
            #     ),  # Configurable max tokens
            # )

            response = await self.client.responses.create(
                model=os.getenv("LEAD_AI_MODEL") or "gpt-4o-mini",
                reasoning={"effort": "low"},
                input=messages,
            )

            content = response.output_text.strip() if response.output_text else ""

            # Validate that the response is valid JSON
            try:
                json.loads(content)
                return content
            except json.JSONDecodeError:
                # If the response is not valid JSON, try to extract JSON from the response
                json_match = re.search(r"\{.*\}", content, re.DOTALL)
                if json_match:
                    return json_match.group(0)
                else:
                    # Fallback: create a simple structured response
                    return json.dumps(
                        {
                            "execution_steps": [
                                {
                                    "step_number": 1,
                                    "step_description": "Analyze the task goal",
                                    "execution_context": f"The task goal: {task_goal}",
                                    "expected_output": "What this step should produce",
                                    "depends_on_steps": [],
                                }
                            ]
                        }
                    )

        except Exception as e:
            logger.error(f"Error decomposing task: {str(e)}")
            # Return a fallback structured response
            return json.dumps(
                {
                    "execution_steps": [
                        {
                            "step_number": 1,
                            "step_description": "Error occurred during task decomposition",
                            "execution_context": f"Original task goal: {task_goal}",
                            "expected_output": "Manual intervention required",
                            "depends_on_steps": [],
                        }
                    ]
                }
            )

    async def _can_agent_handle_task_alone(self, task_goal: str, agent: Agent) -> str:
        """
        Use LLM to determine if a single agent can handle the task alone.
        """
        system_prompt = """You are an AI task assessment assistant. Your job is to determine 
        if a given agent can handle a specific task completely by itself without requiring 
        decomposition into multiple subtasks or coordination with other agents.

        Consider the agent's capabilities, description, and the complexity of the task.
        
        Respond with only "YES" if the agent can handle the task alone, or "NO" if the task 
        requires decomposition or multiple agents."""

        # Format capabilities and skills properly
        capabilities = agent.agent_card.capabilities
        if isinstance(capabilities, dict):
            cap_strings = [f"{k}: {v}" for k, v in capabilities.items()]
        else:
            cap_strings = []

        skills = agent.agent_card.skills
        if isinstance(skills, list):
            skill_names = [
                (s.name or s.id or "Unknown") if isinstance(s, dict) else str(s)
                for s in skills
            ]
        else:
            skill_names = []

        user_prompt = f"""Task: {task_goal}

Agent Name: {agent.agent_card.name}
Agent Description: {agent.agent_card.description}
Agent Capabilities: {", ".join(cap_strings)}
Agent Skills: {", ".join(skill_names)}

Can this agent handle the task completely by itself without requiring decomposition or help from other agents?"""

        try:
            response = await self.client.chat.completions.create(
                model=os.getenv("LEAD_AI_MODEL") or "gpt-4o-mini",
                messages=[
                    ChatCompletionSystemMessageParam(
                        role="system", content=system_prompt
                    ),
                    ChatCompletionUserMessageParam(role="user", content=user_prompt),
                ],
                max_completion_tokens=2048,
            )

            content = response.choices[0].message.content.strip().upper()
            logger.info(f"Agent capability assessment user prompt: {user_prompt}")
            logger.info(f"Agent capability assessment response: {response}")
            logger.info(f"Agent capability assessment response: {content}")
            return content

        except Exception as e:
            logger.error(f"Error assessing agent capability: {str(e)}")
            return "NO"  # Default to decomposition if assessment fails

    async def select_best_agent_for_task(
        self, meta_task_description: str, agents: list[Agent]
    ) -> str:
        """
        Use LLM to select the best agent for a child task from candidate agents

        Args:
            meta_task_description: Description of the meta task
            agents: List of candidate agents with their details

        Returns:
            ID of the selected agent
        """
        system_prompt = """You are an AI tasked with selecting the most appropriate agent for a given task.
        Analyze the task description and the capabilities of each candidate agent, then select the best match.
        Return only the ID of the best matching agent."""

        # Prepare agent descriptions
        agent_descriptions = []

        for i, agent in enumerate(agents):
            agent_desc = f"Agent {i + 1}:\n"

            agent_id = agent.agent_id
            card = agent.agent_card

            name = card.name
            description = card.description

            capabilities = card.capabilities
            if isinstance(capabilities, dict):
                cap_strings = [f"{k}: {v}" for k, v in capabilities.items()]
            else:
                cap_strings = []

            skills = card.skills
            if isinstance(skills, list):
                skill_names = [
                    (s.name or s.id or "Unknown") if isinstance(s, dict) else str(s)
                    for s in skills
                ]
            else:
                skill_names = []

            agent_desc += f"ID: {agent_id}\n"
            agent_desc += f"Name: {name}\n"
            agent_desc += f"Description: {description}\n"
            agent_desc += f"Capabilities: {', '.join(cap_strings)}\n"
            agent_desc += f"Skills: {', '.join(skill_names)}\n"

            agent_descriptions.append(agent_desc)

        agents_text = "\n\n".join(agent_descriptions)

        prompt = f"""Task Description: {meta_task_description}

Available Agents:
{agents_text}

Based on the task description and agent capabilities, which agent (by ID) would be most appropriate for this task?
"""

        messages = [
            ChatCompletionSystemMessageParam(role="system", content=system_prompt),
            ChatCompletionUserMessageParam(role="user", content=prompt),
        ]

        try:
            response = await self.client.chat.completions.create(
                model=os.getenv("CLASSIFIER_AI_MODEL") or "gpt-4o-mini",
                messages=messages,
            )

            content = (
                response.choices[0].message.content.strip()
                if response.choices[0].message.content
                else ""
            )

            # Extract agent ID
            for agent in agents:
                if agent.agent_id in content:
                    return agent.agent_id

            # If no exact match found, return first agent ID
            return agents[0].agent_id
        except Exception as e:
            logger.error(f"Error selecting best agent: {str(e)}")
            return agents[0].agent_id  # Default to first agent

    async def summarize_meta_task_for_base_task(
        self,
        base_task_goal: str,
        meta_task_summaries: list[str],
        meta_task_descriptions: list[str],
    ) -> str:
        """
        Summarize the meta task results for a base task using OpenAI.

        Args:
            base_task_goal: The original goal/objective of the base task
            meta_task_summaries: List of summaries from each meta task
            meta_task_execution_contexts: List of execution contexts from each meta task
            meta_task_descriptions: List of task descriptions from each meta task
            meta_task_histories: List of conversation histories from each meta task

        Returns:
            Comprehensive summary that addresses the original base task goal
        """
        system_prompt = """You are an expert AI agent tasked with combining the output from multiple subtasks into a comprehensive final answer.
        
        IMPORTANT: Extract and combine the output from each subtask.
        
        Your role is to:
        1. Extract the output from each subtask
        2. Combine these outputs into a coherent, comprehensive response
        
        The final answer should:
        - Contain the actual detailed information from each subtask (specific recommendations, data, plans, etc.)
        - Be the complete answer the user is looking for
        - Include specific details, numbers, recommendations, and concrete information
        """

        # Prepare detailed information for each meta task
        detailed_meta_task_info = []
        for i, (summary, description) in enumerate(
            zip(meta_task_summaries, meta_task_descriptions, strict=False)
        ):
            task_info = f"Subtask {i + 1}:\n"
            task_info += f"Description: {description}\n"
            task_info += f"Summary: {summary}\n"

            detailed_meta_task_info.append(task_info)

        # Combine all information
        all_task_info = "\n\n".join(detailed_meta_task_info)

        prompt = f"""Original Task Goal: {base_task_goal}

Detailed Subtask Information:
{all_task_info}

IMPORTANT: Extract and combine the output from each subtask above. Do NOT describe what each subtask did or provide meta-summaries.

Please provide the comprehensive final answer that:
1. Contains the output from all subtasks
2. Combines the output into one complete answer
3. Gives the user the actual information they need

Your response should be an complete answer with all the specific details the user is looking for, extracted directly from the subtask outputs.
"""

        messages = [
            ChatCompletionSystemMessageParam(role="system", content=system_prompt),
            ChatCompletionUserMessageParam(role="user", content=prompt),
        ]

        try:
            # response = await self.client.chat.completions.create(
            #     model=os.getenv("LEAD_AI_MODEL") or "gpt-4o-mini",
            #     messages=messages,
            #     temperature=0.3,  # Lower temperature for more consistent and focused output
            #     max_tokens=int(
            #         os.getenv("SUMMARY_MAX_TOKENS", "4096")
            #     ),  # Allow for comprehensive summary
            # )
            response = await self.client.responses.create(
                model=os.getenv("LEAD_AI_MODEL") or "gpt-4o-mini",
                reasoning={"effort": "low"},
                input=messages,
            )

            content = response.output_text.strip() if response.output_text else ""

            if not content:
                return "Unable to generate summary due to empty response from AI model."

            return content

        except Exception as e:
            print(f"Error summarizing meta task for base task: {str(e)}")
            return f"Error generating comprehensive summary: {str(e)}"

    async def short_debate_with_openai(
        self, original_userinput: str, other_agent_answer: str
    ) -> str:
        """
        Let OpenAI (Lead_ai) generate an updated response based on other agent's answer.
        """
        system_prompt = "You are an expert AI agent participating in a debate."
        prompt = (
            f"Original user input: {original_userinput}\n\n"
            f"These are the solutions to the problem from other agents: {other_agent_answer}\n"
            "Based off the opinion of other agents, can you give an updated response . . ."
        )
        messages = [
            ChatCompletionSystemMessageParam(role="system", content=system_prompt),
            ChatCompletionUserMessageParam(role="user", content=prompt),
        ]
        try:
            response = await self.client.chat.completions.create(
                model=os.getenv("LEAD_AI_MODEL") or "gpt-4o-mini", messages=messages
            )
            return (
                response.choices[0].message.content
                if response.choices[0].message.content
                else ""
            )
        except Exception as e:
            print(f"Error in debate_with_openai: {str(e)}")
            return f"Error: {str(e)}"

    async def long_debate_with_openai(
        self, original_userinput: str, other_agent_answer: str
    ) -> str:
        """
        Let OpenAI (Lead_ai) generate an updated response based on other agent's answer.
        """
        system_prompt = "You are an expert AI agent participating in a debate."
        prompt = (
            f"Original user input: {original_userinput}\n\n"
            f"These are the solutions to the problem from other agents: {other_agent_answer}\n"
            "Using the opinion of other agents as additional advice, can you give an updated response . . ."
        )
        messages = [
            ChatCompletionSystemMessageParam(role="system", content=system_prompt),
            ChatCompletionUserMessageParam(role="user", content=prompt),
        ]
        try:
            response = await self.client.chat.completions.create(
                model=os.getenv("LEAD_AI_MODEL") or "gpt-4o-mini", messages=messages
            )
            return (
                response.choices[0].message.content
                if response.choices[0].message.content
                else ""
            )
        except Exception as e:
            print(f"Error in debate_with_openai: {str(e)}")
            return f"Error: {str(e)}"

    async def summarize_debate_answer(self, messages: list[str]) -> str:
        """
        Summarize the answers from multiple AI agents into a single summary using Lead_ai.
        """
        system_prompt = "You are an expert AI agent tasked with summarizing the debate answers from multiple agents into a concise and comprehensive summary."
        answers_text = "\n\n".join(
            [f"Agent {i + 1}: {msg}" for i, msg in enumerate(messages)]
        )
        prompt = (
            f"Here are the answers from different agents:\n{answers_text}\n\n"
            "Please provide a summary that captures the main points and consensus (if any) from these answers."
        )
        chat_messages = [
            ChatCompletionSystemMessageParam(role="system", content=system_prompt),
            ChatCompletionUserMessageParam(role="user", content=prompt),
        ]
        try:
            response = await self.client.chat.completions.create(
                model=os.getenv("LEAD_AI_MODEL") or "gpt-4o-mini",
                messages=chat_messages,
            )
            return (
                response.choices[0].message.content
                if response.choices[0].message.content
                else ""
            )
        except Exception as e:
            print(f"Error in summarize_debate_answer: {str(e)}")
            return f"Error: {str(e)}"

    async def generate_chat_context(
        self, user_input: str, agent_response: str, context_data: ContextData
    ) -> str:
        """
        Generate a precise context summary for maintaining conversation state.
        """

        system_prompt = """You are an expert context summarizer for multi-agent conversations. Your goal is to maintain a comprehensive, evolving context summary that preserves essential information across conversation turns.

OBJECTIVES:
1. Update and refine the existing context with new information
2. Preserve key entities, decisions, and user preferences
3. Maintain important technical details and specifications
4. Track the conversation's progression and current state
5. Remove outdated or contradicted information
6. Synthesize related information into coherent summaries

APPROACH:
- If previous context exists, intelligently merge it with new information
- Prioritize recent developments while retaining relevant historical context
- Identify and resolve any contradictions between old and new information
- Focus on actionable information and user goals
- Maintain clarity and logical organization

OUTPUT: Return a comprehensive, well-organized context summary that captures the complete conversation state."""

        prompt_parts = [
            "**NEW INTERACTION:**",
            f"User Input: {user_input}",
            f"Agent Response: {agent_response}",
        ]

        if (
            context_data
            and context_data.context_content
            and context_data.context_content.strip()
        ):
            prompt_parts.extend(
                [
                    "",
                    "**EXISTING CONTEXT:**",
                    context_data.context_content,
                    "",
                    "**TASK:** Update and refine the existing context by intelligently incorporating the new interaction. Merge related information, resolve contradictions, and ensure the summary reflects the current conversation state.",
                ]
            )
        else:
            prompt_parts.extend(
                [
                    "",
                    "**TASK:** Create a comprehensive initial context summary based on this first interaction.",
                ]
            )

        prompt = "\n".join(prompt_parts)

        messages = [
            ChatCompletionSystemMessageParam(role="system", content=system_prompt),
            ChatCompletionUserMessageParam(role="user", content=prompt),
        ]

        try:
            response = await self.client.responses.create(
                model=os.getenv("LEAD_AI_MODEL") or "gpt-4o-mini",
                reasoning={"effort": "low"},
                input=messages,
            )

            context_summary = (
                response.output_text.strip() if response.output_text else ""
            )

            if not context_summary:
                context_summary = f"User discussed: {user_input}. Agent provided: {agent_response[:200]}..."

            return context_summary

        except Exception as e:
            print(f"Error in generate_chat_context: {str(e)}")
            existing_context = (
                context_data.context_content
                if context_data and context_data.context_content
                else ""
            )
            fallback = f"{existing_context}\n\nLatest: User: {user_input} | Agent: {agent_response[:200]}..."
            return fallback.strip()

    async def generate_room_memory_content(
        self, messages: list[RoomAgentMessage], room_memory_content: MemoryContent
    ) -> str:
        """
        Generate room memory content using OpenAI based on agent messages and existing memory content.
        """

        # Comprehensive system prompt for room memory generation
        system_prompt = """You are an expert room memory content generator for multi-agent conversation rooms. Your task is to create and maintain a comprehensive memory summary that captures the essential information from agent interactions.

OBJECTIVES:
1. Analyze agent messages and extract key information, decisions, and outcomes
2. Update existing room memory with new information from recent messages
3. Maintain continuity of important context across conversations
4. Preserve critical technical details, user preferences, and agent capabilities
5. Track the progression of tasks, problems solved, and current status
6. Remove outdated or contradicted information

APPROACH:
- If existing memory exists, intelligently merge it with new information from messages
- Prioritize recent developments while retaining relevant historical context
- Focus on actionable information and collaborative outcomes
- Maintain clear organization and logical flow
- Identify patterns in agent collaboration and user interactions

OUTPUT: Return a comprehensive, well-organized memory summary that captures the complete room state and conversation history."""

        # Prepare message content for analysis
        message_summaries = []
        for msg in messages:
            try:
                # Extract agent message content
                agent_content = ""
                if (
                    msg.message_content
                    and msg.message_content.message_task
                    and msg.message_content.message_task.history
                ):
                    # Get the latest agent message from history
                    agent_messages = [
                        m
                        for m in msg.message_content.message_task.history
                        if m.role == Role.agent
                    ]

                    if agent_messages:
                        latest_message = agent_messages[-1]
                        if latest_message.parts and len(latest_message.parts) > 0:
                            # Extract text from the first part
                            agent_content = latest_message.parts[0].root.text

                if agent_content:
                    # Safely construct agent display without assuming agent_name exists
                    _agent_id = getattr(msg, "agent_id", "unknown-agent")
                    _agent_name = getattr(msg, "agent_name", None)
                    _agent_display = (
                        f"{_agent_name} ({_agent_id})"
                        if _agent_name
                        else f"{_agent_id}"
                    )
                    message_summary = f"Agent {_agent_display} at {msg.message_created_at}: {agent_content[:500]}..."
                    message_summaries.append(message_summary)

            except Exception as e:
                print(f"Error processing message {msg.message_id}: {str(e)}")
                continue

        # Build the prompt
        prompt_parts = ["**RECENT AGENT MESSAGES:**"]

        if message_summaries:
            prompt_parts.extend(message_summaries)
        else:
            prompt_parts.append("No recent agent messages to process.")

        prompt_parts.append("")

        # Include existing memory if available
        if (
            room_memory_content
            and room_memory_content.memory_text
            and room_memory_content.memory_text.strip()
        ):
            prompt_parts.extend(
                [
                    "**EXISTING ROOM MEMORY:**",
                    room_memory_content.memory_text,
                    "",
                    "**TASK:** Update and enhance the existing room memory by incorporating the new agent messages above. Merge related information, resolve any contradictions, and ensure the memory reflects the current state of the room.",
                ]
            )
        else:
            prompt_parts.extend(
                [
                    "**TASK:** Create a comprehensive initial room memory summary based on the agent messages above."
                ]
            )

        prompt = "\n".join(prompt_parts)

        messages_for_ai = [
            ChatCompletionSystemMessageParam(role="system", content=system_prompt),
            ChatCompletionUserMessageParam(role="user", content=prompt),
        ]

        try:
            response = await self.client.responses.create(
                model=os.getenv("LEAD_AI_MODEL") or "gpt-4o-mini",
                reasoning={"effort": "low"},
                input=messages_for_ai,
            )

            memory_content = (
                response.output_text.strip() if response.output_text else ""
            )

            # Basic validation
            if not memory_content:
                # Fallback if AI doesn't return content
                existing_memory = (
                    room_memory_content.memory_text
                    if room_memory_content and room_memory_content.memory_text
                    else ""
                )
                fallback_content = f"{existing_memory}\n\nUpdated with {len(message_summaries)} new agent messages."
                return fallback_content.strip()

            return memory_content

        except Exception as e:
            print(f"Error in generate_room_memory_content: {str(e)}")
            # Provide a useful fallback
            existing_memory = (
                room_memory_content.memory_text
                if room_memory_content and room_memory_content.memory_text
                else ""
            )
            fallback = f"{existing_memory}\n\nProcessed {len(message_summaries)} agent messages."
            return fallback.strip()

    async def parse_user_message_by_llm(
        self, message_text: str, room_agent_set: dict = None, is_debate_mode: bool = False
    ) -> dict:
        """
        Parse user message using LLM with intelligent task decomposition.
        
        Process:
        1. Analyze if task needs decomposition
        2. If complex, break into logical steps
        3. Assign agents based on mentions or task nature
        
        Debate mode: Skip decomposition, generate linear chain
        """
        
        room_agent_set = room_agent_set or {}
        
        # === DEBATE MODE ===
        if is_debate_mode:
            # Extract mentions from message
            mention_pattern = r"<@([^|]+)\|([^>]+)>"
            mentions = re.findall(mention_pattern, message_text)
            
            # Determine agents for debate
            if mentions:
                # Use mentioned agents
                debate_agents = [
                    {
                        "agent_id": agent_id.strip(),
                        "agent_name": agent_name.strip(),
                    }
                    for agent_id, agent_name in mentions
                    if agent_id.strip() in room_agent_set
                ]
                message_type = "DEBATE_WITH_MENTIONS"
            else:
                # Use all room agents
                debate_agents = [
                    {
                        "agent_id": agent_id,
                        "agent_name": agent_name,
                    }
                    for agent_name, agent_id in room_agent_set.items()
                ]
                message_type = "DEBATE_NO_MENTIONS"
            
            if not debate_agents:
                raise ValueError("No agents available for debate mode")
            
            # Remove mentions from content
            clean_content = message_text
            for match in re.finditer(mention_pattern, message_text):
                clean_content = clean_content.replace(match.group(0), "")
            clean_content = re.sub(r'\s+', ' ', clean_content).strip()
            
            # Generate debate rounds
            num_rounds = settings.debate_rounds or 3
            task_steps = []
            previous_step_id = None
            
            step_counter = 1
            for round_num in range(1, num_rounds + 1):
                for agent in debate_agents:
                    step_id = f"step_{step_counter}"
                    
                    # Use unified format: agent_id, agent_name, task_content
                    task_step = {
                        "step_id": step_id,
                        "agent_id": agent["agent_id"],
                        "agent_name": agent["agent_name"],
                        "task_content": clean_content,  # Changed from step_content
                        "dependencies": [previous_step_id] if previous_step_id else [],
                    }
                    
                    task_steps.append(task_step)
                    previous_step_id = step_id
                    step_counter += 1
            
            result = {
                "message_type": message_type,
                "original_text": message_text,
                "needs_decomposition": False,
                "task_steps": task_steps  # Unified format
            }
            
            logger.info(
                f"Generated debate chain: {len(debate_agents)} agents × "
                f"{num_rounds} rounds = {len(task_steps)} tasks"
            )
            
            return result
        
        # === NORMAL MODE: Enhanced with decomposition decision ===
        agent_list = ""
        if room_agent_set:
            agent_list = "\n".join([
                f"- Agent ID: {aid}, Name: {aname}"
                for aid, aname in room_agent_set.items()
            ])
        
        system_prompt = """You are an expert task analyzer and decomposer for multi-agent collaboration systems.
                Your job is to analyze user messages, decide if decomposition is needed, then assign agents.

                PROCESS:
                1. ANALYZE TASK COMPLEXITY
                - Simple task: Single action, can be completed in one step
                - Complex task: Multiple logical steps, dependencies between actions
                
                2. DECIDE DECOMPOSITION
                - If simple: Keep as single step
                - If complex: Break into logical sub-tasks with clear dependencies
                
                3. ASSIGN AGENTS
                - ONLY assign agents that are explicitly mentioned in the message using <@agent-id|agent-name> format
                - If NO mentions in message: ALL agent_id MUST be null (do not auto-assign)
                - If agents mentioned: Use ONLY those mentioned agents
                - Extract agent_id and agent_name from mention format

                CRITICAL RULE: 
                NO agent mentions in message = ALL agent_id = null
                Do NOT auto-assign agents based on task type or capabilities if not mentioned.

                SCENARIOS:

                1. NO MENTIONS + SIMPLE TASK
                Example: "Help me analyze the data"
                → Single step, agent_id = null, agent_name = null

                2. NO MENTIONS + COMPLEX TASK
                Example: "Create a complete data analysis report including data cleaning, statistical analysis and visualization"
                → Decompose into steps: [cleaning, analysis, visualization]
                → ALL steps: agent_id = null, agent_name = null

                3. SINGLE MENTION + SIMPLE TASK
                Example: "<@agent-1|Analyst> Analyze sales data"
                → Single step, agent_id = "agent-1", agent_name = "Analyst"

                4. SINGLE MENTION + COMPLEX TASK
                Example: "<@agent-1|Developer> Build a complete web application including frontend, backend and database"
                → Decompose into steps, ALL assign to agent-1

                5. MULTIPLE MENTIONS + TASK
                Example: "<@agent-1|Analyst> Analyze data, then <@agent-2|Designer> Create visualization"
                → Assign steps based on which agent is mentioned near that task
                → Step 1: agent_id = "agent-1"
                → Step 2: agent_id = "agent-2"

                OUTPUT STRUCTURE (strict JSON):
                {
                "message_type": "NO_MENTIONS" | "SINGLE_MENTION" | "MULTIPLE_MENTIONS",
                "original_text": "original message",
                "needs_decomposition": true | false,
                "decomposition_reason": "why decomposed or not" | null,
                "task_steps": [
                    {
                    "step_id": "step_1",
                    "agent_id": "uuid" | null,
                    "agent_name": "name" | null,
                    "task_content": "what to do (clean text, remove all <@...> mentions)",
                    "dependencies": ["step_id", ...]
                    }
                ]
                }

                DECOMPOSITION GUIDELINES:
                - Break by logical phases (prepare → execute → review)
                - Break by functional areas (data → analysis → visualization)
                - Break by sequential dependencies (A must complete before B)
                - Keep steps granular but not too fine (3-7 steps optimal)
                - Each step should be independently executable

                DEPENDENCY RULES:
                - Empty [] = no dependencies, can start immediately
                - ["step_1"] = depends on step_1 completing
                - ["step_1", "step_2"] = depends on both (usually use last one)

                AGENT ASSIGNMENT RULES:
                - Mention format: <@uuid|name>
                - If NO <@...> in original message: agent_id = null for ALL steps
                - If mentions present: Extract agent_id and agent_name from mentions
                - Remove all <@...> from task_content (clean text only)
                - Match mentioned agents to appropriate task steps based on position/context

                Output valid JSON only, no explanation."""

        user_prompt = f"""Analyze this task and create execution plan:

                Available agents in room:
                {agent_list if agent_list else "None"}

                User message:
                "{message_text}"

                Steps:
                1. Is this a simple or complex task?
                2. Should it be decomposed? Why or why not?
                3. What are the logical steps (if decomposed)?
                4. Which agents should handle each step?

                Output JSON with your analysis."""

        try:  
            messages = [
                ChatCompletionSystemMessageParam(role="system", content=system_prompt),
                ChatCompletionUserMessageParam(role="user", content=user_prompt)
            ]
            
            response = await self.client.chat.completions.create(
                model=os.getenv("LEAD_AI_MODEL") or "gpt-4o-mini",
                messages=messages,
                temperature=0.3,
                response_format={"type": "json_object"}
            )
            
            content = response.choices[0].message.content
            if not content:
                raise ValueError("Empty response from LLM")
            
            result = json.loads(content)
            
            # Log decomposition decision
            needs_decomp = result.get("needs_decomposition", False)
            decomp_reason = result.get("decomposition_reason", "")
            steps_count = len(result.get("task_steps", []))
            
            logger.info(
                f"LLM analysis: {result.get('message_type')}, "
                f"decomposition={'YES' if needs_decomp else 'NO'} "
                f"({decomp_reason}), {steps_count} steps"
            )
            
            return result
            
        except Exception as e:
            logger.error(f"LLM parsing failed: {e}")
            raise


openai_service = OpenAIService()
