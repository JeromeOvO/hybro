import json
import os
import re
from typing import Any

from dotenv import load_dotenv
from openai import AsyncOpenAI
from openai.types.chat import (
    ChatCompletionSystemMessageParam,
    ChatCompletionUserMessageParam,
)

from common.utils.logger import get_logger
from models.agent import Agent
from models.memory import ChatContext, ContextData
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

    async def decompose_task(self, base_task: BaseTask, context_data: ContextData) -> str:
        """
        Decompose a base task into subtasks using OpenAI.
        Returns a JSON string with structured execution steps.
        """
        system_prompt = """You are a task decomposition assistant. Your job is
        to break down complex tasks into smaller, manageable steps that can 
        be solved more easily and effectively. 
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

            content = (
                response.output_text.strip()
                if response.output_text
                else ""
            )

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

            content = (
                response.output_text.strip()
                if response.output_text
                else ""
            )

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

    async def generate_chat_context(self, user_input: str, agent_response: str, context_data: ContextData) -> str:
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
        
        if context_data and context_data.context_content and context_data.context_content.strip():
            prompt_parts.extend([
                "",
                "**EXISTING CONTEXT:**",
                context_data.context_content,
                "",
                "**TASK:** Update and refine the existing context by intelligently incorporating the new interaction. Merge related information, resolve contradictions, and ensure the summary reflects the current conversation state."
            ])
        else:
            prompt_parts.extend([
                "",
                "**TASK:** Create a comprehensive initial context summary based on this first interaction."
            ])
        
        prompt = "\n".join(prompt_parts)
        
        messages = [
            ChatCompletionSystemMessageParam(role="system", content=system_prompt),
            ChatCompletionUserMessageParam(role="user", content=prompt),
        ]
        
        try:
            response = await self.client.responses.create(
                model=os.getenv("LEAD_AI_MODEL") or "gpt-4o-mini",
                reasoning={"effort": "low"},
                input=messages  
            )
            
            context_summary = response.output_text.strip() if response.output_text else ""
            
            if not context_summary:
                context_summary = f"User discussed: {user_input}. Agent provided: {agent_response[:200]}..."
            
            return context_summary
            
        except Exception as e:
            print(f"Error in generate_chat_context: {str(e)}")
            existing_context = context_data.context_content if context_data and context_data.context_content else ""
            fallback = f"{existing_context}\n\nLatest: User: {user_input} | Agent: {agent_response[:200]}..."
            return fallback.strip()

openai_service = OpenAIService()
