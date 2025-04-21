from abc import ABC, abstractmethod
import json
from typing import Dict, Any, List, Optional, Callable, Union, Type
import asyncio
from uuid import uuid4
from datetime import datetime

# Import the A2A Protocol models
from models.protocol import (
    Message, Task, TaskState, TaskStatus, Artifact, Part, TextPart,
    JSONRPCRequest, JSONRPCResponse, JSONRPCError,
    SendTaskRequest, SendTaskResponse, GetTaskRequest, GetTaskResponse,
    CancelTaskRequest, CancelTaskResponse, TaskIdParams, TaskSendParams,
    InvalidRequestError, MethodNotFoundError, TaskNotFoundError,
    AgentCard, AgentSkill, AgentCapabilities, AgentProvider, AgentAuthentication
)

# A2A Agent Interface
class AgentInterface(ABC):
    """All agents that integrate with A2A must implement this interface"""
    
    @abstractmethod
    async def process_task(self, task_params: TaskSendParams) -> Task:
        """Process a task and return the result"""
        pass
    
    @abstractmethod
    def get_agent_id(self) -> str:
        """Get the agent's unique identifier"""
        pass
    
    @abstractmethod
    def get_agent_card(self) -> AgentCard:
        """Get the agent's capabilities card"""
        pass
    
    @abstractmethod
    async def cancel_task(self, task_id: str) -> Task:
        """Cancel a task that's in progress"""
        pass

# A2A Server Implementation
class A2AServer(ABC):
    """Base A2A Server class for receiving and handling A2A protocol requests"""
    
    def __init__(self, agent: AgentInterface):
        self.agent = agent
        self.agent_id = agent.get_agent_id()
        self.tasks: Dict[str, Task] = {}
        self.handlers = {
            "tasks/send": self._handle_send_task,
            "tasks/get": self._handle_get_task,
            "tasks/cancel": self._handle_cancel_task,
        }
    
    async def handle_request(self, request_json: str) -> str:
        """Process an incoming JSON-RPC request and return a JSON response"""
        try:
            # Parse the JSON request
            request_data = json.loads(request_json)
            
            # Basic validation
            if "jsonrpc" not in request_data or request_data.get("jsonrpc") != "2.0":
                return self._create_error_response(None, InvalidRequestError())
            
            req_id = request_data.get("id")
            method = request_data.get("method")
            
            if not method:
                return self._create_error_response(req_id, InvalidRequestError(message="Method is required"))
            
            # Find the appropriate handler
            if method in self.handlers:
                return await self.handlers[method](request_data)
            else:
                return self._create_error_response(req_id, MethodNotFoundError())
                
        except json.JSONDecodeError:
            return self._create_error_response(None, InvalidRequestError(message="Invalid JSON"))
        except Exception as e:
            # Generic error handling
            return self._create_error_response(
                None, 
                JSONRPCError(code=-32603, message=f"Internal error: {str(e)}")
            )
    
    async def _handle_send_task(self, request_data: Dict[str, Any]) -> str:
        """Handle a task/send request"""
        req_id = request_data.get("id")
        try:
            # Parse the request as SendTaskRequest
            params = request_data.get("params", {})
            task_params = TaskSendParams(**params)
            
            # Process the task with the agent
            task = await self.agent.process_task(task_params)
            
            # Store the task
            self.tasks[task.id] = task
            
            # Create response
            response = SendTaskResponse(
                id=req_id,
                result=task
            )
            return json.dumps(response.model_dump())
            
        except Exception as e:
            return self._create_error_response(
                req_id,
                JSONRPCError(code=-32602, message=f"Error processing task: {str(e)}")
            )
    
    async def _handle_get_task(self, request_data: Dict[str, Any]) -> str:
        """Handle a task/get request"""
        req_id = request_data.get("id")
        try:
            params = TaskIdParams(**request_data.get("params", {}))
            task_id = params.id
            
            if task_id not in self.tasks:
                return self._create_error_response(req_id, TaskNotFoundError())
            
            response = GetTaskResponse(
                id=req_id,
                result=self.tasks[task_id]
            )
            return json.dumps(response.model_dump())
            
        except Exception as e:
            return self._create_error_response(
                req_id,
                JSONRPCError(code=-32602, message=f"Error getting task: {str(e)}")
            )
    
    async def _handle_cancel_task(self, request_data: Dict[str, Any]) -> str:
        """Handle a task/cancel request"""
        req_id = request_data.get("id")
        try:
            params = TaskIdParams(**request_data.get("params", {}))
            task_id = params.id
            
            if task_id not in self.tasks:
                return self._create_error_response(req_id, TaskNotFoundError())
            
            # Call the agent to cancel the task
            task = await self.agent.cancel_task(task_id)
            
            # Update the stored task
            self.tasks[task_id] = task
            
            response = CancelTaskResponse(
                id=req_id,
                result=task
            )
            return json.dumps(response.model_dump())
            
        except Exception as e:
            return self._create_error_response(
                req_id,
                JSONRPCError(code=-32602, message=f"Error canceling task: {str(e)}")
            )
    
    def _create_error_response(self, req_id: Optional[Any], error: JSONRPCError) -> str:
        """Create a JSON-RPC error response"""
        response = JSONRPCResponse(
            id=req_id,
            error=error
        )
        return json.dumps(response.model_dump())
    
    @abstractmethod
    async def start(self):
        """Start the server to listen for messages"""
        pass
    
    @abstractmethod
    async def stop(self):
        """Stop the server"""
        pass

# HTTP Server implementation of A2A Server
class A2AHTTPServer(A2AServer):
    """HTTP implementation of A2A Server"""
    
    def __init__(self, agent: AgentInterface, host: str = "localhost", port: int = 8000):
        super().__init__(agent)
        self.host = host
        self.port = port
        self.server = None
    
    async def start(self):
        """Start the HTTP server to listen for A2A requests"""
        from aiohttp import web
        
        app = web.Application()
        app.router.add_post("/", self._handle_http_request)
        app.router.add_get("/agent-card", self._handle_agent_card_request)
        
        self.server = await web._run_app(app, host=self.host, port=self.port)
        print(f"A2A Server started at http://{self.host}:{self.port}")
    
    async def stop(self):
        """Stop the HTTP server"""
        if self.server:
            await self.server.shutdown()
            self.server = None
    
    async def _handle_http_request(self, request):
        """Handle incoming HTTP requests"""
        from aiohttp import web
        
        try:
            # Read the request body
            body = await request.text()
            
            # Process the request
            response_json = await self.handle_request(body)
            
            # Return the response
            return web.Response(
                text=response_json,
                content_type="application/json"
            )
        except Exception as e:
            # Handle unexpected errors
            error_response = self._create_error_response(
                None,
                JSONRPCError(code=-32603, message=f"HTTP handler error: {str(e)}")
            )
            return web.Response(
                text=error_response,
                content_type="application/json",
                status=500
            )
    
    async def _handle_agent_card_request(self, request):
        """Handle requests for the agent capability card"""
        from aiohttp import web
        
        try:
            # Get the agent card
            agent_card = self.agent.get_agent_card()
            
            # Return the card as JSON
            return web.Response(
                text=json.dumps(agent_card.model_dump()),
                content_type="application/json"
            )
        except Exception as e:
            return web.Response(
                text=json.dumps({"error": f"Failed to get agent card: {str(e)}"}),
                content_type="application/json",
                status=500
            )

# Helper class to create AI agent responses
class AIAgentHelper:
    """Helper class to make it easier for AI agents to create A2A-compatible responses"""
    
    @staticmethod
    def create_text_response(task_id: str, session_id: str, text: str) -> Task:
        """Create a simple text response task"""
        # Create a text part
        text_part = TextPart(
            type="text",
            text=text
        )
        
        # Create an artifact with the text
        artifact = Artifact(
            name="response",
            parts=[text_part]
        )
        
        # Create a completed task status
        status = TaskStatus(
            state=TaskState.COMPLETED,
            timestamp=datetime.now()
        )
        
        # Return the complete task
        return Task(
            id=task_id,
            sessionId=session_id,
            status=status,
            artifacts=[artifact]
        )
    
    @staticmethod
    def create_agent_card(
        name: str, 
        description: str, 
        skills: List[Dict[str, Any]],
        organization: str = "AI Agent",
        url: str = "",
        streaming: bool = False
    ) -> AgentCard:
        """Create an agent capability card"""
        
        # Create the skills
        agent_skills = []
        for skill in skills:
            agent_skills.append(AgentSkill(
                id=skill.get("id", str(uuid4())),
                name=skill.get("name", ""),
                description=skill.get("description", ""),
                tags=skill.get("tags", []),
                examples=skill.get("examples", []),
                inputModes=skill.get("inputModes", ["text"]),
                outputModes=skill.get("outputModes", ["text"])
            ))
        
        # Create the agent card
        return AgentCard(
            name=name,
            description=description,
            url=url,
            provider=AgentProvider(
                organization=organization,
                url=url
            ),
            version="1.0.0",
            capabilities=AgentCapabilities(
                streaming=streaming,
                pushNotifications=False,
                stateTransitionHistory=True
            ),
            skills=agent_skills
        )

# Simple implementation of an AI agent
class SimpleAIAgent(AgentInterface):
    """A simple AI agent implementation that can be extended"""
    
    def __init__(self, agent_id: str, name: str, description: str, skills: List[Dict[str, Any]]):
        self.id = agent_id
        self.name = name
        self.description = description
        self.skills = skills
        self.tasks: Dict[str, Task] = {}
    
    def get_agent_id(self) -> str:
        return self.id
    
    def get_agent_card(self) -> AgentCard:
        return AIAgentHelper.create_agent_card(
            name=self.name,
            description=self.description,
            skills=self.skills
        )
    
    async def process_task(self, task_params: TaskSendParams) -> Task:
        """Process a task and generate a response"""
        # Create a task ID if not provided
        task_id = task_params.id
        
        # Get the user message
        message = task_params.message
        
        # Default implementation - just echo the message
        text_content = ""
        for part in message.parts:
            if part.type == "text":
                text_content += part.text
        
        # Create a response
        task = AIAgentHelper.create_text_response(
            task_id=task_id,
            session_id=task_params.sessionId,
            text=f"Echo: {text_content}"
        )
        
        # Store the task
        self.tasks[task_id] = task
        
        return task
    
    async def cancel_task(self, task_id: str) -> Task:
        """Cancel a task that's in progress"""
        if task_id not in self.tasks:
            raise ValueError(f"Task {task_id} not found")
        
        # Get the task
        task = self.tasks[task_id]
        
        # Update the status to canceled
        task.status.state = TaskState.CANCELED
        task.status.timestamp = datetime.now()
        
        return task
