from openai import AsyncOpenAI
from config import settings
from typing import List, Dict, Any

class OpenAIService:
    def __init__(self):
        self.client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    
    async def get_embedding(self, text: str) -> List[float]:
        """Get embedding for text using OpenAI's embedding model"""
        response = await self.client.embeddings.create(
            model=settings.EMBEDDING_MODEL,
            input=text
        )
        return response.data[0].embedding
    
    async def lead_ai_completion(self, query: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """Generate task breakdown with the Lead AI"""
        messages = [
            {"role": "system", "content": "You are a Lead AI responsible for breaking down complex tasks into steps. Each step should be clearly defined with a goal. Respond in JSON format."},
            {"role": "user", "content": f"Break down the following task into clear steps: {query}. Format the response as JSON."}
        ]
        
        if context:
            messages.append({"role": "user", "content": f"Additional context: {context}. Remember to format as JSON."})
        
        response = await self.client.chat.completions.create(
            model=settings.LEAD_AI_MODEL,
            messages=messages,
            response_format={"type": "json_object"},
            functions=[{
                "name": "task_breakdown",
                "description": "Break down a task into steps with their descriptions",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "goal": {"type": "string", "description": "The overall goal of the task"},
                        "steps": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "step_id": {"type": "string"},
                                    "description": {"type": "string"},
                                    "input_requirements": {"type": "string"}
                                },
                                "required": ["step_id", "description"]
                            }
                        }
                    },
                    "required": ["goal", "steps"]
                }
            }],
            function_call={"name": "task_breakdown"}
        )
        
        return response.choices[0].message.function_call.arguments
    
    async def classifier_ai_completion(self, step_description: str) -> Dict[str, Any]:
        """Identify capabilities needed for a step using Classifier AI"""
        messages = [
            {"role": "system", "content": "You are a Classifier AI responsible for identifying the capabilities needed for a task step. Respond in JSON format."},
            {"role": "user", "content": f"Identify the capabilities needed for this step: {step_description}. Format your response as JSON."}
        ]
        
        response = await self.client.chat.completions.create(
            model=settings.CLASSIFIER_AI_MODEL,
            messages=messages,
            response_format={"type": "json_object"},
            functions=[{
                "name": "identify_capabilities",
                "description": "Identify capabilities needed for a task step",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "capabilities": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of capabilities needed for this step"
                        },
                        "priority": {
                            "type": "string", 
                            "enum": ["low", "medium", "high"],
                            "description": "Priority level of this step"
                        }
                    },
                    "required": ["capabilities"]
                }
            }],
            function_call={"name": "identify_capabilities"}
        )
        
        return response.choices[0].message.function_call.arguments
    
    async def agent_completion(self, agent_model: str, prompt: str, context: Dict[str, Any] = None) -> str:
        """Get completion from an agent model"""
        messages = [
            {"role": "system", "content": "You are an AI assistant helping with a specific task."},
            {"role": "user", "content": prompt}
        ]
        
        if context:
            messages.append({"role": "user", "content": f"Context: {context}"})
        
        response = await self.client.chat.completions.create(
            model=agent_model,
            messages=messages
        )
        
        return response.choices[0].message.content

    async def chat_completion(self, messages, model="gpt-4o", json_response=False):
        try:
            # Always ensure json is mentioned when requesting JSON format
            if json_response:
                # Check if json is mentioned in any message
                has_json_mention = any("json" in str(m.get("content", "")).lower() for m in messages)
                
                if not has_json_mention:
                    # Add to system message if it exists
                    system_found = False
                    for i, message in enumerate(messages):
                        if message["role"] == "system":
                            messages[i]["content"] += " Please provide your response in JSON format."
                            system_found = True
                            break
                    
                    # If no system message, add to last user message
                    if not system_found:
                        for i in reversed(range(len(messages))):
                            if messages[i]["role"] == "user":
                                messages[i]["content"] += " Please format your response as JSON."
                                break
                        else:
                            # If no user message found, add a new system message
                            messages.insert(0, {"role": "system", "content": "Please provide your response in JSON format."})
            
            # Log messages for debugging
            print(f"Making OpenAI request with messages: {messages}")
            
            response = await self.client.chat.completions.create(
                model=model,
                messages=messages,
                response_format={"type": "json_object"} if json_response else None,
                temperature=0.7,
            )
            
            return response.choices[0].message.content
        except Exception as e:
            print(f"Error in chat completion: {str(e)}")
            raise

openai_service = OpenAIService() 