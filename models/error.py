from typing import List, Optional, Any
from pydantic import BaseModel, Field
from a2a.types import TaskState, AgentCard


class Error(BaseModel):
    status_code: int
    error_code: str
    error_message: str
    error_data: Optional[Any] = None

# Fix: Create proper exception classes
class AgentNotFoundError(Exception):
    def __init__(self, message: str = "Agent not found"):
        self.message = message
        super().__init__(self.message)

class AgentNotAssignedError(Exception):
    def __init__(self, message: str = "Agent not assigned"):
        self.message = message
        super().__init__(self.message)

class AgentCardRequiredError(Exception):
    def __init__(self, message: str = "Agent card is required"):
        self.message = message
        super().__init__(self.message)

class AgentIdRequiredError(Exception):
    def __init__(self, message: str = "Agent ID is required"):
        self.message = message
        super().__init__(self.message)

class QueryTextRequiredError(Exception):
    def __init__(self, message: str = "Query text is required"):
        self.message = message
        super().__init__(self.message)

class TaskIdRequiredError(Exception):
    def __init__(self, message: str = "Task ID is required"):
        self.message = message
        super().__init__(self.message)

class ParentTaskIdRequiredError(Exception):
    def __init__(self, message: str = "Parent task ID is required"):
        self.message = message
        super().__init__(self.message)

class SessionIdRequiredError(Exception):
    def __init__(self, message: str = "Session ID is required"):
        self.message = message
        super().__init__(self.message)

class IllgalParameterError(Exception):
    def __init__(self, message: str = "Illegal parameter"):
        self.message = message
        super().__init__(self.message)

class A2AServiceError(Exception):
    def __init__(self, message: str = "A2A service error"):
        self.message = message
        super().__init__(self.message)

class TaskNotFoundError(Exception):
    def __init__(self, message: str = "Task not found"):
        self.message = message
        super().__init__(self.message)

class AgentAlreadyAssignedError(Exception):
    def __init__(self, message: str = "Agent already assigned"):
        self.message = message
        super().__init__(self.message)

# Keep original Pydantic models for response
class ErrorResponse(BaseModel):
    status_code: int
    error_code: str
    error_message: str
    error_data: Optional[Any] = None