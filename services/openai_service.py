import json
import os
import re

from a2a.types import Role
from dotenv import load_dotenv
from openai import AsyncOpenAI
from openai.types.chat import (
    ChatCompletionSystemMessageParam,
    ChatCompletionUserMessageParam,
)

from common.utils.logger import get_logger
from config.settings import settings
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

    async def expand_query_for_discovery(self, query: str) -> str:
        """
        Use LLM to semantically expand short queries for better agent discovery matching.
        
        Args:
            query: The original user query
            
        Returns:
            Expanded query with synonyms, related terms, and use case context
        """
        query = query.strip()
        word_count = len(query.split())
        
        # Expand short queries
        if word_count > settings.discovery_query_expansion_threshold:
            return query
        
        system_prompt = """You are a query expansion assistant for an AI agent discovery system.
Your job is to expand short user queries into more descriptive, semantically rich queries
that will help find relevant AI agents.

EXPANSION RULES:
1. Add context about what the user might be looking for
2. Expand the query into more detailed sentences, not phrases.
3. Add use case context (e.g., "help with", "specializes in", "can do")
4. Keep the original intent but make it more descriptive
5. Don't change the core meaning

EXAMPLES:
- "story" → "The agent supports storytelling, narrative writing, creative writing assistance, and fiction writing help."
- "data" → "The agent supports data analysis, data processing, data visualization, and generating actionable insights from data."

Return ONLY the expanded query, no explanations."""

        user_prompt = f"""Expand this query for better agent discovery:
"{query}"

Provide an expanded version that includes synonyms, related terms, and use case context."""

        try:
            messages = [
                ChatCompletionSystemMessageParam(role="system", content=system_prompt),
                ChatCompletionUserMessageParam(role="user", content=user_prompt),
            ]
            
            response = await self.client.chat.completions.create(
                model=os.getenv("LEAD_AI_MODEL") or "gpt-4o-mini",
                messages=messages,
                temperature=0.3,  # Lower temperature for more consistent expansions
                max_tokens=100,  # Keep expansions concise
            )
            
            expanded = response.choices[0].message.content.strip()
            
            # Fallback to original if expansion fails
            if not expanded or len(expanded) < len(query):
                logger.warning("Query expansion returned invalid result, using original query")
                return query
                
            logger.info(f"Query expanded: '{query}' → '{expanded[:100]}...'")
            return expanded
            
        except Exception as e:
            logger.warning(f"Query expansion failed: {e}, using original query")
            return query

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
        - CRITICAL: If the task explicitly states a number of tasks/agents (e.g., "exactly 2 tasks", "2 agents"), 
          you MUST create exactly that many execution steps. Do not add planning, validation, or orchestration steps.
        3.	Merge-and-Prune:
        - Merge overlapping or redundant subtasks.
        - Prune infeasible, irrelevant, or duplicate branches. 
        Important: Max 8 steps (unless explicitly constrained to fewer).
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

    async def summarize_agent_responses(
        self,
        agent_responses: list[dict[str, str]],
        mode: str = "non_debate",
    ) -> str:
        """
        Summarize the answers from multiple AI agents into a single summary using Lead_ai.

        Args:
            agent_responses: List of dicts with 'agent_name' and 'message' keys
                Example: [{"agent_name": "Research Agent", "message": "..."}, ...]
            mode: Summary mode - "debate" or "non_debate"
                - "debate": Compares viewpoints, highlights agreements/disagreements
                - "non_debate": Combines contributions into a unified response

        Returns:
            Summary text string
        """
        # Get max words from env variable, default to 200 words
        max_words = int(os.getenv("SUMMARY_MAX_WORDS", "200"))

        # Select system prompt based on mode
        if mode == "debate":
            system_prompt = """You are an expert debate summarizer for multi-agent systems. Your task is to analyze responses from multiple AI agents and create a structured summary that captures different perspectives, agreements, and disagreements.

CORE OBJECTIVES:
1. Extract and organize distinct viewpoints from each agent
2. Identify areas of consensus and disagreement
3. Highlight key insights and actionable recommendations
4. Maintain agent attribution for all points
5. Present information in a clear, structured format

ANALYSIS APPROACH:
- Compare agent responses to identify overlapping vs. unique points
- Note where agents build upon each other's ideas
- Identify contradictions or alternative approaches
- Extract specific data, recommendations, or conclusions from each agent
- Synthesize complementary information into coherent themes

QUALITY STANDARDS:
- Use actual agent names, never generic labels
- Include specific details, data, and reasoning from agents
- Keep summary concise but comprehensive
- Focus on substance over process
- Ensure balanced representation of all agent contributions"""

            user_prompt_template = (
                "Here are responses from multiple agents with potentially different opinions:\n\n{answers}\n\n"
                "Summarize the key points from all agents in a structured format. "
                "Use the actual agent names when referencing their opinions. "
                "Keep the summary within {max_words} words."
            )
        else:
            # Default: non_debate mode
            system_prompt = """You are an expert synthesizer for multi-agent collaboration systems. Your task is to combine responses from multiple AI agents into a unified, coherent summary that presents the complete answer to the user's question.

CORE OBJECTIVES:
1. Synthesize all agent contributions into a unified response
2. Combine complementary information without redundancy
3. Present a clear, actionable final answer
4. Highlight the most important insights and recommendations
5. Create a seamless narrative that flows naturally

Synthesis APPROACH:
- Identify how each agent's contribution adds to the complete answer
- Merge overlapping information, keeping the most detailed version
- Organize information in a logical flow (context → analysis → recommendations)
- Ensure all key points from each agent are represented
- Remove redundancy while preserving unique insights

QUALITY STANDARDS:
- Create a unified voice (not a list of "Agent X said...")
- Focus on delivering value to the user
- Prioritize actionable insights and clear conclusions
- Keep the summary concise but complete
- Only attribute to specific agents when their unique expertise is relevant"""

            user_prompt_template = (
                "Here are responses from multiple agents working together on the user's request:\n\n{answers}\n\n"
                "Synthesize these into a single, unified response that combines all their contributions. "
                "Present it as a cohesive answer, not as a comparison of opinions. "
                "Keep the summary within {max_words} words."
            )

        # Format agent responses
        answers_text = "\n\n".join(
            [
                f"--- {resp.get('agent_name', 'Unknown Agent')} ---\n{resp.get('message', '')}"
                for resp in agent_responses
            ]
        )

        user_prompt = user_prompt_template.format(
            answers=answers_text,
            max_words=max_words,
        )

        chat_messages = [
            ChatCompletionSystemMessageParam(role="system", content=system_prompt),
            ChatCompletionUserMessageParam(role="user", content=user_prompt),
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
            logger.error(f"Error in summarize_agent_responses (mode={mode}): {str(e)}")
            return f"Error: {str(e)}"

    # Backwards-compatible aliases
    async def summarize_debate_answer(
        self, agent_responses: list[dict[str, str]]
    ) -> str:
        """Alias for summarize_agent_responses with mode='debate'."""
        return await self.summarize_agent_responses(agent_responses, mode="debate")

    async def summarize_non_debate_answer(
        self, agent_responses: list[dict[str, str]]
    ) -> str:
        """Alias for summarize_agent_responses with mode='non_debate'."""
        return await self.summarize_agent_responses(agent_responses, mode="non_debate")

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
        self,
        message_text: str,
        selected_agent_set: dict = None,
        is_debate_mode: bool = False,
        auto_assign_agents: bool = False,
        agents: list[Agent] = None,
    ) -> dict:
        """
        Parse user message using LLM with intelligent task decomposition.

        Process:
        1. Analyze if task needs decomposition
        2. If complex, break into logical steps
        3. Assign agents based on mentions or task nature

        Args:
            message_text: The user's message
            selected_agent_set: Dict of {agent_id: agent_name} chosen for this request
            is_debate_mode: Whether to use debate mode
            auto_assign_agents: If True (Auto mode), LLM will assign agents from pool
                               If False (Curated mode), only assign if @mentioned

        Debate mode: Skip decomposition, generate linear chain
        """

        # Canonical shape for selected_agent_set is {agent_id: agent_name}
        selected_agent_set = selected_agent_set or {}

        # === DEBATE MODE ===
        if is_debate_mode:
            # Extract mentions from message
            mention_pattern = r"<@([^|]+)\|([^>]+)>"
            mentions = re.findall(mention_pattern, message_text)

            # Determine agents for debate
            if mentions:
                # Use mentioned agents. We trust the ID from the mention and
                # prefer the name stored in selected_agent_set when available.
                debate_agents = []
                for agent_id, agent_name in mentions:
                    agent_id = agent_id.strip()
                    agent_name = agent_name.strip()

                    # Only consider agents that are actually in the room
                    if agent_id in selected_agent_set:
                        debate_agents.append(
                            {
                                "agent_id": agent_id,
                                "agent_name": selected_agent_set.get(
                                    agent_id, agent_name
                                ),
                            }
                        )

                message_type = "DEBATE_WITH_MENTIONS"
            else:
                # Use all agents from the selected_agent_set mapping
                debate_agents = [
                    {
                        "agent_id": agent_id,
                        "agent_name": agent_name,
                    }
                    for agent_id, agent_name in selected_agent_set.items()
                ]
                message_type = "DEBATE_NO_MENTIONS"

            if not debate_agents:
                raise ValueError("No agents available for debate mode")

            # Remove mentions from content
            clean_content = message_text
            for match in re.finditer(mention_pattern, message_text):
                clean_content = clean_content.replace(match.group(0), "")
            clean_content = re.sub(r"\s+", " ", clean_content).strip()

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
                "task_steps": task_steps,  # Unified format
            }

            logger.info(
                f"Generated debate chain: {len(debate_agents)} agents × "
                f"{num_rounds} rounds = {len(task_steps)} tasks"
            )

            return result

        # === NORMAL MODE: Enhanced with decomposition decision ===
        agent_list = ""
        if agents:
            # Build detailed agent descriptions with capabilities and skills
            agent_descriptions = []
            for agent in agents:
                agent_desc = f"- Agent ID: {agent.agent_id}\n"
                agent_desc += f"  Name: {agent.agent_card.name}\n"
                agent_desc += f"  Description: {agent.agent_card.description or 'No description'}\n"
                
                # Format capabilities
                capabilities = agent.agent_card.capabilities
                if isinstance(capabilities, dict):
                    cap_strings = [f"{k}: {v}" for k, v in capabilities.items()]
                    agent_desc += f"  Capabilities: {', '.join(cap_strings)}\n"
                
                # Format skills
                skills = agent.agent_card.skills
                if isinstance(skills, list) and skills:
                    skill_names = [
                        (s.name or s.id or "Unknown") if hasattr(s, 'name') else str(s)
                        for s in skills
                    ]
                    agent_desc += f"  Skills: {', '.join(skill_names)}\n"
                
                agent_descriptions.append(agent_desc)
            agent_list = "\n".join(agent_descriptions)
        elif selected_agent_set:
            # Fallback to basic agent info if full Agent objects not provided
            agent_list = "\n".join(
                [
                    f"- Agent ID: {aid}, Name: {aname}"
                    for aid, aname in selected_agent_set.items()
                ]
            )

        # Build different prompts based on auto_assign_agents mode
        if auto_assign_agents and selected_agent_set:
            # AUTO MODE: Automatically assign best agents from pool
            system_prompt = f"""You are an expert task analyzer and decomposer for multi-agent collaboration systems.
                Your job is to analyze user messages, decide if decomposition is needed, then assign the most appropriate agents.

                AVAILABLE AGENTS:
                {agent_list}

                PROCESS:
                1. ANALYZE TASK COMPLEXITY
                - Simple task: Single action, can be completed in one step
                - Complex task: Multiple logical steps, dependencies between actions
                
                2. DECIDE DECOMPOSITION
                - If simple: Keep as single step
                - If complex: Break into logical sub-tasks with clear dependencies
                
                3. ASSIGN AGENTS (AUTO MODE)
                - You MUST assign an agent from the available pool to EACH step
                - Choose the most appropriate agent based on their description, capabilities, skills, and the task content
                - DIVERSIFY agent assignment: Different steps should ideally use different agents based on their specializations
                - Match agent capabilities and skills to task requirements - read each agent's description carefully
                - If only one agent is available, assign that agent to all steps
                - If task mentions a specific agent with <@...>, prioritize that agent for relevant steps

                CRITICAL RULES:
                - EVERY step MUST have an agent_id and agent_name assigned from the available agents
                - Do NOT leave agent_id as null - always pick the best matching agent
                - If unsure, assign the first available agent
                - If "needs_decomposition" is false, there MUST be exactly one task step

                OUTPUT STRUCTURE (strict JSON):
                {{
                "message_type": "AUTO_ASSIGNED" | "SINGLE_MENTION" | "MULTIPLE_MENTIONS",
                "original_text": "original message",
                "needs_decomposition": true | false,
                "decomposition_reason": "why decomposed or not" | null,
                "task_steps": [
                    {{
                    "step_id": "step_1",
                    "agent_id": "uuid from available agents",
                    "agent_name": "name from available agents",
                    "task_content": "what to do (clean text, remove all <@...> mentions)",
                    "dependencies": ["step_id", ...]
                    }}
                ]
                }}

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

                Output valid JSON only, no explanation."""
        else:
            # CURATED MODE: Only assign if explicitly mentioned
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
                ADDITIONAL CRITICAL RULE: If "needs_decomposition" is false, there MUST be exactly one task step, and its "task_content" MUST be the "original_text" with all <@...> mentions removed.
                
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
                ChatCompletionUserMessageParam(role="user", content=user_prompt),
            ]

            response = await self.client.chat.completions.create(
                model=os.getenv("LEAD_AI_MODEL") or "gpt-4o-mini",
                messages=messages,
                # temperature=0.3,
                response_format={"type": "json_object"},
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

    async def analyze_message_routing(
        self, message_text: str, candidate_agents: list[Agent]
    ) -> dict:
        """
        Analyze a user message and decide the optimal routing strategy.

        Args:
            message_text: The user's message to analyze
            candidate_agents: List of candidate agents from vector search

        Returns:
            Dict with:
            - strategy: "single" | "parallel" | "sequential"
            - agent_ids: List of selected agent IDs
            - agent_reasons: Dict mapping agent_id to reason for selection
            - reasoning: Overall explanation of the routing decision
            - needs_debate: Whether debate mode would benefit this conversation
        """
        # Build agent descriptions for the prompt
        agent_descriptions = []
        for agent in candidate_agents:
            skills = (
                ", ".join([s.name for s in agent.agent_card.skills])
                if agent.agent_card.skills
                else "General assistance"
            )
            agent_descriptions.append(
                f"- Agent ID: {agent.agent_id}\n"
                f"  Name: {agent.agent_card.name}\n"
                f"  Description: {agent.agent_card.description}\n"
                f"  Skills: {skills}"
            )

        agents_info = "\n".join(agent_descriptions)

        system_prompt = """You are an intelligent message router for a multi-agent AI system.
Your job is to analyze user messages and decide the optimal routing strategy.

ROUTING STRATEGIES:
1. "single" - Route to ONE best agent
   Use when: Simple question, single domain, straightforward task
   Example: "What's the weather?" -> Weather agent only

2. "parallel" - Route to 2-3 agents simultaneously
   Use when: Question benefits from multiple perspectives, comparison needed, or touches multiple domains
   Example: "Compare Python and Rust for web development" -> Python expert AND Rust expert
   Example: "Help me plan a healthy meal that's also budget-friendly" -> Nutrition agent AND Finance agent

3. "sequential" - Route to agents in a specific order (dependency chain)
   Use when: Task has clear steps where one agent's output feeds into another
   Example: "Research this topic, then write a blog post about it" -> Researcher -> Writer

DECISION FACTORS:
- Task complexity (simple vs multi-faceted)
- Domain coverage (single domain vs cross-domain)
- Need for multiple perspectives
- Dependency between subtasks

OUTPUT FORMAT (strict JSON):
{
    "strategy": "single" | "parallel" | "sequential",
    "agent_ids": ["agent-id-1", "agent-id-2"],
    "agent_reasons": {
        "agent-id-1": "Why this agent was selected",
        "agent-id-2": "Why this agent was selected"
    },
    "reasoning": "Brief explanation of the routing decision",
    "needs_debate": true | false
}

Set needs_debate=true if:
- Question is subjective or opinion-based
- Multiple valid approaches exist
- User might benefit from seeing different perspectives discussed

Output valid JSON only."""

        user_prompt = f"""Analyze this message and decide the routing strategy:

USER MESSAGE:
{message_text}

AVAILABLE AGENTS:
{agents_info}

Decide which agent(s) should handle this message and why."""

        try:
            messages = [
                ChatCompletionSystemMessageParam(role="system", content=system_prompt),
                ChatCompletionUserMessageParam(role="user", content=user_prompt),
            ]

            response = await self.client.chat.completions.create(
                model=os.getenv("LEAD_AI_MODEL") or "gpt-4o-mini",
                messages=messages,
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content
            if not content:
                raise ValueError("Empty response from LLM")

            result = json.loads(content)

            logger.info(
                f"Routing analysis: strategy={result.get('strategy')}, "
                f"agents={len(result.get('agent_ids', []))}, "
                f"debate={result.get('needs_debate')}"
            )

            return result

        except Exception as e:
            logger.error(f"Routing analysis failed: {e}")
            # Return safe default
            if candidate_agents:
                first_agent = candidate_agents[0]
                return {
                    "strategy": "single",
                    "agent_ids": [first_agent.agent_id],
                    "agent_reasons": {
                        first_agent.agent_id: "Fallback to best match due to analysis error"
                    },
                    "reasoning": f"Fallback selection due to error: {str(e)}",
                    "needs_debate": False,
                }
            return {
                "strategy": "single",
                "agent_ids": [],
                "agent_reasons": {},
                "reasoning": "No agents available",
                "needs_debate": False,
            }


openai_service = OpenAIService()
