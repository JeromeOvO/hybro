from typing import List, Optional, Any
from pydantic import BaseModel, Field
from a2a.types import TaskState, AgentCard


class Error(BaseModel):
    status_code: int
    error_code: str
    error_message: str
    error_data: Optional[Any] = None

class AgentNotFoundError(Error):
    status_code: int = 200
    error_code: str = "agent_not_found"
    error_message: str = "Agent not found"
    error_data: Optional[Any] = None

class AgentCardRequiredError(Error):
    status_code: int = 200
    error_code: str = "agent_card_required"
    error_message: str = "Agent card is required"
    error_data: Optional[Any] = None

class AgentIdRequiredError(Error):
    status_code: int = 200
    error_code: str = "agent_id_required"
    error_message: str = "Agent ID is required"
    error_data: Optional[Any] = None

class QueryTextRequiredError(Error):
    status_code: int = 200
    error_code: str = "query_text_required"
    error_message: str = "Query text is required"
    error_data: Optional[Any] = None

class TaskIdRequiredError(Error):
    status_code: int = 200
    error_code: str = "task_id_required"
    error_message: str = "Task ID is required"
    error_data: Optional[Any] = None

class ParentTaskIdRequiredError(Error):
    status_code: int = 200
    error_code: str = "parent_task_id_required"
    error_message: str = "Parent task ID is required"
    error_data: Optional[Any] = None

class SessionIdRequiredError(Error):
    status_code: int = 200
    error_code: str = "session_id_required"
    error_message: str = "Session ID is required"
    error_data: Optional[Any] = None

class IllgalParameterError(Error):
    status_code: int = 200
    error_code: str = "illgal_parameter"
    error_message: str = "Illegal parameter"
    error_data: Optional[Any] = None

class A2AServiceError(Error):
    status_code: int = 200
    error_code: str = "a2a_service_error"
    error_message: str = "A2A service error"
    error_data: Optional[Any] = None