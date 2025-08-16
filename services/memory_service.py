from uuid import uuid4
from datetime import datetime

from models.memory import ChatContext, ContextData
from services.database_service import DatabaseService   
from services.openai_service import OpenAIService
from models.response import ChatMemoryResponse
from models.request import ChatMemoryRequest
from models.error import SessionIdRequiredError


# Chat Memory Service Manager
class ChatMemoryService:
    def __init__(self):
        self.database_service = DatabaseService()
        self.openai_service = OpenAIService()

    # Chat Contexts
    async def create_chat_context(self, request: ChatMemoryRequest) -> ChatMemoryResponse:
        """
        Create a chat context in the database
        """

        try:
            new_chat_context = ChatContext(
                context_id=str(uuid4()),  # Generate a unique context_id
                user_name=request.user_name,
                session_id=request.session_id,
                context_data=ContextData(
                    context_content=request.user_input if request.user_input is not None else ""
                ),
                created_at=datetime.now(),
                updated_at=datetime.now(),
                extend_info=[]
            )
            success = await self.database_service.add_chat_context(new_chat_context)
            if success:
                return ChatMemoryResponse(
                    user_name=request.user_name,
                    chat_context=new_chat_context,
                    success=True, 
                    error=None, 
                    status_code=200
                )
            else:
                return ChatMemoryResponse(
                    user_name=request.user_name,
                    success=False, 
                    error="Failed to add chat context", 
                    status_code=500
                )
        except Exception as e:
            return ChatMemoryResponse(
                user_name=request.user_name,
                success=False, 
                error=str(e), 
                status_code=500
            )
    
    async def get_chat_context_by_session_id(self, request: ChatMemoryRequest) -> ChatMemoryResponse:
        """
        Get a chat context by session_id
        """

        if request.session_id is None:
            raise SessionIdRequiredError()
        
        try:
            chat_context = await self.database_service.get_chat_context_by_session_id(request.session_id)
            if chat_context:
                return ChatMemoryResponse(
                    user_name=request.user_name,
                    success=True, 
                    error=None, 
                    status_code=200, 
                    chat_context=chat_context
                )
            else:
                return ChatMemoryResponse(
                    user_name=request.user_name,
                    success=False, 
                    error="Chat context not found", 
                    status_code=404
                )
        except Exception as e:
            return ChatMemoryResponse(
                user_name=request.user_name,
                success=False, 
                error=str(e), 
                status_code=500
            )
    
    async def update_chat_context_by_session_id(self, request: ChatMemoryRequest) -> ChatMemoryResponse:
        """
        Update a chat context by session_id
        """

        if request.session_id is None:
            raise SessionIdRequiredError()
        
        try:
            chat_context = await self.database_service.get_chat_context_by_session_id(request.session_id)
        except Exception as e:
            return ChatMemoryResponse(
                user_name=request.user_name,
                success=False, 
                error=str(e), 
                status_code=500
            )
        
        new_context_data = await self.openai_service.generate_chat_context(request.user_input, request.agent_response, chat_context.context_data)
        
        
        try:
            chat_context = ChatContext(
                context_id=chat_context.context_id,  # Generate a unique context_id
                user_name=request.user_name,
                session_id=request.session_id,
                context_data=ContextData(
                    context_content=new_context_data
                ),
                created_at=chat_context.created_at,
                updated_at=datetime.now(),
                extend_info=chat_context.extend_info
            )
            success = await self.database_service.update_chat_context_by_session_id(request.session_id, chat_context)
            if success:
                return ChatMemoryResponse(
                    user_name=request.user_name,
                    success=True, 
                    error=None, 
                    status_code=200
                )
            else:
                return ChatMemoryResponse(
                    user_name=request.user_name,
                    success=False, 
                    error="Failed to update chat context", 
                    status_code=500
                )
        except Exception as e:
            return ChatMemoryResponse(
                user_name=request.user_name,
                success=False, 
                error=str(e), 
                status_code=500
            )
    
    async def delete_chat_context_by_session_id(self, request: ChatMemoryRequest) -> ChatMemoryResponse:
        """
        Delete a chat context by session_id
        """
        try:
            success = await self.database_service.delete_chat_context_by_session_id(request.session_id)
            if success:
                return ChatMemoryResponse(
                    user_name=request.user_name,
                    success=True, 
                    error=None, 
                    status_code=200
                )
            else:
                return ChatMemoryResponse(
                    user_name=request.user_name,
                    success=False, 
                    error="Failed to delete chat context", 
                    status_code=500
                )
        except Exception as e:
            return ChatMemoryResponse(
                user_name=request.user_name,
                success=False, 
                error=str(e), 
                status_code=500
            )
