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