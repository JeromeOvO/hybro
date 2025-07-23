from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionSystemMessageParam, ChatCompletionUserMessageParam
from typing import Dict, Any, List, Optional, TYPE_CHECKING
import json
import time
from datetime import datetime
import uuid
from models.task import BaseTask, MetaTask
from dotenv import load_dotenv
import os
load_dotenv()
import logging
import json
import re

logger = logging.getLogger(__name__)

class OpenAIService:
    def __init__(self):
        self.client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    async def get_embedding(self, text: str, target_dim: Optional[int] = None) -> List[float]:
        """Get embedding for text
        
        Args:
            text: The text to embed
            target_dim: Optional target dimension to resize the embedding to
        
        Returns:
            List of embedding values (original or resized)
        """
        response = await self.client.embeddings.create(
            input=text,
            model="text-embedding-ada-002"  # Keep using the same model
        )
        
        embedding = response.data[0].embedding
        
        return embedding
    

    async def decompose_task(self, base_task: BaseTask) -> str:
        """
        Decompose a base task into subtasks using OpenAI.
        Returns a JSON string with structured execution steps.
        """
        system_prompt = """You are an AI tasked with decomposing a base task into detailed execution steps.
        Analyze the task goal and create a structured execution plan with specific steps.
        
        Return the response in the following JSON format:
        {
            "execution_steps": [
                {
                    "step_number": 1,
                    "step_description": "Detailed description of what this step accomplishes",
                    "execution_content": "Specific actions, queries, or reasoning needed to complete this step",
                    "expected_output": "What this step should produce or determine"
                }
            ]
        }
        
        Each step should be:
        1. Specific and actionable
        2. Clear enough for an AI to understand and execute
        3. Include reasoning or analysis if needed
        4. Build upon previous steps when applicable
        """

        task_goal = base_task.task.history[0].parts[0].root.text if base_task.task.history else "No task goal provided"
        
        prompt = f"""Task Goal: {task_goal}

Please decompose this task into a structured execution plan with specific steps.
Each step should be detailed enough for an AI agent to understand and execute.
Consider the logical flow and dependencies between steps.
"""

        messages = [
            ChatCompletionSystemMessageParam(role="system", content=system_prompt),
            ChatCompletionUserMessageParam(role="user", content=prompt)
        ]
        
        try:
            response = await self.client.chat.completions.create(
                model=os.getenv("LEAD_AI_MODEL") or "gpt-4o-mini",
                messages=messages,
                temperature=0.3,  # Lower temperature for more consistent structured output
                max_tokens=4096
            )
            
            content = response.choices[0].message.content.strip() if response.choices[0].message.content else ""
            
            # Validate that the response is valid JSON
            try:

                json.loads(content)
                return content
            except json.JSONDecodeError:
                # If the response is not valid JSON, try to extract JSON from the response
                json_match = re.search(r'\{.*\}', content, re.DOTALL)
                if json_match:
                    return json_match.group(0)
                else:
                    # Fallback: create a simple structured response
                    return json.dumps({
                        "execution_steps": [
                            {
                                "step_number": 1,
                                "step_description": "Analyze the task goal",
                                "execution_content": f"Review and understand the task goal: {task_goal}",
                                "expected_output": "Clear understanding of what needs to be accomplished"
                            }
                        ]
                    })
                    
        except Exception as e:
            logger.error(f"Error decomposing task: {str(e)}")
            # Return a fallback structured response
            return json.dumps({
                "execution_steps": [
                    {
                        "step_number": 1,
                        "step_description": "Error occurred during task decomposition",
                        "execution_content": f"Original task goal: {task_goal}",
                        "expected_output": "Manual intervention required"
                    }
                ]
            })

    async def select_best_agent_for_task(self, child_task_description: str, agents: List[Dict[str, Any]]) -> str:
        """
        Use LLM to select the best agent for a child task from candidate agents
        
        Args:
            child_task_description: Description of the child task
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
            agent_desc = f"Agent {i+1}:\n"
            agent_desc += f"ID: {agent['agent_id']}\n"
            agent_desc += f"Name: {agent.get('agentCard', {}).get('name', 'Unknown')}\n"
            agent_desc += f"Description: {agent.get('agentCard', {}).get('description', 'No description')}\n"
            
            # Handle capabilities - in Agent model it's a dict, not a list
            capabilities = agent.get('agentCard', {}).get('capabilities', {})
            if isinstance(capabilities, dict):
                cap_strings = [f"{k}: {v}" for k, v in capabilities.items()]
            else:
                cap_strings = capabilities if isinstance(capabilities, list) else []
            agent_desc += f"Capabilities: {', '.join(cap_strings)}\n"
            
            # Add skills if available
            skills = agent.get('agentCard', {}).get('skills', [])
            if skills:
                skill_names = [skill.get('name', skill.get('id', 'Unknown')) for skill in skills]
                agent_desc += f"Skills: {', '.join(skill_names)}\n"
            
            agent_desc += f"Similarity Score: {agent.get('score', 0)}\n"
            agent_desc += f"Remote: {agent.get('is_remote', False)}\n"
            agent_descriptions.append(agent_desc)
        
        agents_text = "\n\n".join(agent_descriptions)
        
        prompt = f"""Task Description: {child_task_description}

Available Agents:
{agents_text}

Based on the task description and agent capabilities, which agent (by ID) would be most appropriate for this task?
"""
        
        messages = [
            ChatCompletionSystemMessageParam(role="system", content=system_prompt),
            ChatCompletionUserMessageParam(role="user", content=prompt)
        ]
        
        try:
            response = await self.client.chat.completions.create(
                model=os.getenv("CLASSIFIER_AI_MODEL") or "gpt-4o-mini",
                messages=messages
            )
            
            content = response.choices[0].message.content.strip() if response.choices[0].message.content else ""
            
            # Extract agent ID
            for agent in agents:
                if agent["agent_id"] in content:
                    return agent["agent_id"]
            
            # If no exact match found, return first agent ID
            return agents[0]["agent_id"]
        except Exception as e:
            logger.error(f"Error selecting best agent: {str(e)}")
            return agents[0]["agent_id"]  # Default to first agent

    async def summarize_meta_task_for_base_task(self, base_task_goal: str, meta_task_summaries: List[str], meta_task_descriptions: List[str]) -> str:
        """
        Summarize the meta task results for a base task using OpenAI.
        
        Args:
            base_task_goal: The original goal/objective of the base task
            meta_task_summaries: List of summaries from each meta task
            meta_task_execution_contents: List of execution contents from each meta task
            meta_task_descriptions: List of task descriptions from each meta task
            meta_task_histories: List of conversation histories from each meta task
            
        Returns:
            Comprehensive summary that addresses the original base task goal
        """
        system_prompt = """You are an expert AI agent tasked with synthesizing results from multiple subtasks into a comprehensive final answer.
        
        Your role is to:
        1. Analyze the original task goal
        2. Review all the subtask execution steps and their results
        3. Consider the conversation histories between agents and users
        4. Create a cohesive, comprehensive summary that directly addresses the original goal
        
        The summary should:
        - Be well-structured and easy to understand
        - Include key insights from each subtask
        - Address the original goal comprehensively
        - Highlight any important findings or conclusions
        - Maintain logical flow and coherence
        """
        
        # Prepare detailed information for each meta task
        detailed_meta_task_info = []
        for i, (summary, description) in enumerate(zip(
            meta_task_summaries, meta_task_descriptions
        )):
            task_info = f"Subtask {i+1}:\n"
            task_info += f"Description: {description}\n"
            task_info += f"Summary: {summary}\n"
            
            detailed_meta_task_info.append(task_info)
        
        # Combine all information
        all_task_info = "\n\n".join(detailed_meta_task_info)
        
        prompt = f"""Original Task Goal: {base_task_goal}

Detailed Subtask Information:
{all_task_info}

Please provide a comprehensive final answer that:
1. Directly addresses the original task goal
2. Synthesizes the key findings from all subtasks
3. Presents a coherent and complete response
4. Highlights the most important insights and conclusions
5. Maintains logical structure and flow

Your response should be the final, comprehensive answer to the original task goal based on all the subtask results.
"""
        
        messages = [
            ChatCompletionSystemMessageParam(role="system", content=system_prompt),
            ChatCompletionUserMessageParam(role="user", content=prompt)
        ]
        
        try:
            response = await self.client.chat.completions.create(
                model=os.getenv("LEAD_AI_MODEL") or "gpt-4o-mini",
                messages=messages,
                temperature=0.3,  # Lower temperature for more consistent and focused output
                max_tokens=4096   # Allow for comprehensive summary
            )
            
            content = response.choices[0].message.content.strip() if response.choices[0].message.content else ""
            
            if not content:
                return "Unable to generate summary due to empty response from AI model."
            
            return content
            
        except Exception as e:
            print(f"Error summarizing meta task for base task: {str(e)}")
            return f"Error generating comprehensive summary: {str(e)}"
    


    async def short_debate_with_openai(self, original_userinput: str, other_agent_answer: str) -> str:
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
            ChatCompletionUserMessageParam(role="user", content=prompt)
        ]
        try:
            response = await self.client.chat.completions.create(
                model=os.getenv("LEAD_AI_MODEL") or "gpt-4o-mini",
                messages=messages
            )
            return response.choices[0].message.content if response.choices[0].message.content else ""
        except Exception as e:
            print(f"Error in debate_with_openai: {str(e)}")
            return f"Error: {str(e)}"
        
    async def long_debate_with_openai(self, original_userinput: str, other_agent_answer: str) -> str:
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
            ChatCompletionUserMessageParam(role="user", content=prompt)
        ]
        try:
            response = await self.client.chat.completions.create(
                model=os.getenv("LEAD_AI_MODEL") or "gpt-4o-mini",
                messages=messages
            )
            return response.choices[0].message.content if response.choices[0].message.content else ""
        except Exception as e:
            print(f"Error in debate_with_openai: {str(e)}")
            return f"Error: {str(e)}"
    

    async def summarize_debate_answer(self, messages: List[str]) -> str:
        """
        Summarize the answers from multiple AI agents into a single summary using Lead_ai.
        """
        system_prompt = "You are an expert AI agent tasked with summarizing the debate answers from multiple agents into a concise and comprehensive summary."
        answers_text = "\n\n".join([f"Agent {i+1}: {msg}" for i, msg in enumerate(messages)])
        prompt = (
            f"Here are the answers from different agents:\n{answers_text}\n\n"
            "Please provide a summary that captures the main points and consensus (if any) from these answers."
        )
        chat_messages = [
            ChatCompletionSystemMessageParam(role="system", content=system_prompt),
            ChatCompletionUserMessageParam(role="user", content=prompt)
        ]
        try:
            response = await self.client.chat.completions.create(
                model=os.getenv("LEAD_AI_MODEL") or "gpt-4o-mini",
                messages=chat_messages
            )
            return response.choices[0].message.content if response.choices[0].message.content else ""
        except Exception as e:
            print(f"Error in summarize_debate_answer: {str(e)}")
            return f"Error: {str(e)}"

openai_service = OpenAIService() 