import json
from collections.abc import AsyncGenerator
from typing import Any
from uuid import uuid4

import httpx
from a2a.client.client import A2ACardResolver, A2AClient
from a2a.types import (
    AgentCard,
    JSONRPCErrorResponse,
    Message,
    MessageSendConfiguration,
    MessageSendParams,
    Role,
    SendMessageRequest,
    SendMessageResponse,
    SendMessageSuccessResponse,
    SendStreamingMessageRequest,
    SendStreamingMessageResponse,
    SendStreamingMessageSuccessResponse,
    TextPart,
)

from common.utils.logger import get_logger
from models.error import A2AServiceError, IllgalParameterError
from models.response import InsepectionCenterConnectionValidationResponse
# from models.streaming_response import (
#     ArtifactUpdateStreamingEvent,
#     #BaseStreamingEvent,
#     CompleteMessageEvent,
#     ErrorStreamingEvent,
#     StatusUpdateStreamingEvent,
#     TaskUpdateStreamingEvent,
#     TokenStreamingEvent,
#     UnknownStreamingEvent,
# )

logger = get_logger(__name__)


class A2AService:
    def __init__(self):
        pass

    async def get_agent_card_from_url(self, agent_url: str) -> AgentCard:
        if not agent_url:
            raise IllgalParameterError()

        try:
            httpx_client = httpx.AsyncClient(timeout=600.0)
            card_resolver = A2ACardResolver(httpx_client, str(agent_url))
            card = await card_resolver.get_agent_card()
            return card

        except Exception as e:
            logger.error(f"Failed to get agent card from url: {e}", exc_info=True)
            raise A2AServiceError() from e

    async def get_a2a_client(self, agent_url: str) -> A2AClient:
        # check if agent url is valid

        if not agent_url:
            raise IllgalParameterError()

        try:
            httpx_client = httpx.AsyncClient(timeout=600.0)
            card_resolver = A2ACardResolver(httpx_client, str(agent_url))
            card = await card_resolver.get_agent_card()
            a2a_client = A2AClient(httpx_client, agent_card=card)

            return a2a_client

        except Exception as e:
            logger.error(f"Failed to initialize a2a client: {e}", exc_info=True)
            raise A2AServiceError() from e
    
    async def create_a2a_client(self, agent_card: AgentCard) -> A2AClient:
        try:
            httpx_client = httpx.AsyncClient(timeout=600.0)
            a2a_client = A2AClient(httpx_client, agent_card=agent_card)
            return a2a_client

        except Exception as e:
            logger.error(f"Failed to initialize a2a client: {e}", exc_info=True)
            raise A2AServiceError() from e

    def has_streaming_capability(self, agent_card: AgentCard) -> bool:
        """
        Check if an agent supports streaming capability.
        
        Args:
            agent_card: The agent's card with capabilities
            
        Returns:
            True if agent supports streaming, False otherwise
        """
        return (
            hasattr(agent_card.capabilities, "streaming")
            and agent_card.capabilities.streaming is True
        )

    async def send_message_sync(
        self, 
        agent_card: AgentCard, 
        message: Message,
    ) -> SendMessageResponse | None:
        """
        Send message to agent using synchronous (non-streaming) endpoint.
        
        Args:
            agent_url: URL of the target agent
            message: A2A Message to send
            a2a_client: Optional pre-initialized A2A client
            
        Returns:
            Task data as dict
            
        Raises:
            A2AServiceError: If sending fails
        """
        
        try:
            # Use provided client or create new one
            a2a_client = await self.create_a2a_client(agent_card)

            payload = MessageSendParams(
                message=message,
                configuration=MessageSendConfiguration(acceptedOutputModes=["text/plain"]),
            )

            send_message_request = SendMessageRequest(
                id=str(uuid4()),
                method="message/send",
                jsonrpc="2.0",
                params=payload,
            )

            logger.info(f"a2a_service: Sending sync message to agent: {agent_card}")
            response = await a2a_client.send_message(send_message_request)
            
            # Handle error
            if isinstance(response.root, JSONRPCErrorResponse):
                error_msg = str(response.root.error.message)
                logger.error(f"a2a_service: Agent error: {error_msg}")
                raise A2AServiceError(error_msg)
            return response
            # # Extract and convert result to dict
            # result = response.root.result
            # # TODO : result is a2a.types.Task
            # # Assume result is a2a.types.Message for now
            # # Only support Text for now
            # if isinstance(result, Message):
            #     responded_message = result

            #     logger.info(f"a2a_service: Received sync response from agent: {agent_card}")
            #     # extract content from message parts
            #     parts = responded_message.parts if hasattr(responded_message, "parts") else []
            #     return TokenStreamingEvent(
            #         content="".join(part.root.text for part in parts if isinstance(part.root, TextPart)),
            #         agent_url=agent_card.url,
            #         task_id=responded_message.task_id if hasattr(responded_message, "task_id") else None,
            #         message_id=responded_message.id if hasattr(responded_message, "id") else None,
            #     )

        except A2AServiceError:
            raise
        except Exception as e:
            logger.error(f"Failed to send sync message: {e}", exc_info=True)
            raise A2AServiceError(str(e)) from e

    async def send_message_streaming(
        self, 
        agent_card: AgentCard, 
        message: Message,
    ) -> AsyncGenerator[SendStreamingMessageResponse, None]:
        """
        Send message to agent with TRUE passthrough streaming.
        
        This method acts as a transparent passthrough - whatever the agent sends
        (whether immediately or after processing), we forward it immediately to
        the caller. No buffering.
        
        Note: This method assumes the agent supports streaming. Use send_message()
        for automatic capability detection.
        
        Args:
            agent_card: The card of the target agent
            message: A2A Message to send
            a2a_client: Optional pre-initialized A2A client
            
        Yields:
            Dict events in our internal format (TokenStreamingEvent, TaskUpdateStreamingEvent, etc.)
        """
        # try:
            # Use provided client or create new one
        a2a_client = await self.create_a2a_client(agent_card)

        payload = MessageSendParams(
            message=message,
            configuration=MessageSendConfiguration(acceptedOutputModes=["text/plain"]),
        )

        stream_request = SendStreamingMessageRequest(
            id=str(uuid4()),
            method="message/stream",
            jsonrpc="2.0",
            params=payload,
        )

        logger.info(f"a2a_service: Starting streaming from agent: {agent_card}")
        response_stream = a2a_client.send_message_streaming(stream_request)
        agent_url = agent_card.url
        # Yield each event IMMEDIATELY as it arrives
        async for response in response_stream:
            yield response
                # Handle errors
                # if isinstance(response.root, JSONRPCErrorResponse):
                #     logger.error(f"a2a_service: Agent error: {response.root.error}")
                #     yield ErrorStreamingEvent(
                #         error=str(response.root.error.message),
                #         code=response.root.error.code,
                #         agent_url=agent_url,
                #     )
                #     return
                
                # # Extract result
                # result = response.root.result
                
                # # Transform A2A protocol to our internal event format
                # if result.kind == "message":
                #     # Incremental message update (streaming tokens)
                #     text = ""
                #     if hasattr(result, "parts") and result.parts:
                #         for part in result.parts:
                #             if hasattr(part, "root") and hasattr(part.root, "text"):
                #                 text += part.root.text
                    
                #     yield TokenStreamingEvent(
                #         content=text,
                #         message_id=result.message_id if hasattr(result, "message_id") else None,
                #         task_id=result.task_id if hasattr(result, "task_id") else None,
                #         agent_url=agent_url,
                #     )
                    
                # elif result.kind == "task":
                #     # result is a2a.types.Task
                #     # Task completion or update
                #     state = "unknown"
                #     if hasattr(result, "status") and hasattr(result.status, "state"):
                #         state = result.status.state
                    
                #     # Convert result to dict
                    
                #     yield TaskUpdateStreamingEvent(
                #         task_id=result.id if hasattr(result, "id") else None,
                #         state=state,
                #         a2a_response=result,
                #         agent_url=agent_url,
                #     )
                    
                #     # Break out when task is completed
                #     if state in ["completed", "failed", "cancelled"]:
                #         logger.info(f"a2a_service: Task {state}, ending stream from agent: {agent_url}")
                    
                # elif result.kind == "status-update":
                #     # Status update event
                #     state = "unknown"
                #     if hasattr(result, "status") and hasattr(result.status, "state"):
                #         state = result.status.state
                    
                #     yield StatusUpdateStreamingEvent(
                #         state=state,
                #         status=result.status if hasattr(result, "status") else None,
                #         agent_url=agent_url,
                #     )
                    
                # elif result.kind == "artifact-update":
                #     # Artifact update
                #     yield ArtifactUpdateStreamingEvent(
                #         artifact=result.artifact if hasattr(result, "artifact") else None,
                #         agent_url=agent_url,
                #     )
                
                # else:
                #     # Unknown event type
                #     logger.warning(f"a2a_service: Unknown event kind: {result.kind}")
                #     yield UnknownStreamingEvent(
                #         kind=result.kind,
                #         data=result,
                #         agent_url=agent_url,
                #     )
            
        #     logger.info(f"a2a_service: Streaming completed from agent: {agent_url}")

        # except Exception as e:
        #     logger.error(f"a2a_service: Streaming error: {e}", exc_info=True)
        #     yield ErrorStreamingEvent(
        #         error=str(e),
        #         agent_url=agent_url,
        #     )

    async def send_message(
        self, 
        agent_card: AgentCard, 
        message: Message
    ) -> AsyncGenerator[SendStreamingMessageResponse | SendMessageResponse, None]:
        """
        Send message to agent with automatic capability detection.
        
        This is the main entry point for sending messages. It will:
        1. Fetch the agent card
        2. Check if agent supports streaming
        3. Call send_message_streaming() OR send_message_sync() accordingly
        4. Always yield events in consistent format
        
        Args:
            agent_url: URL of the target agent
            message: A2A Message to send
            
        Yields:
            Dict events (TokenStreamingEvent, TaskUpdateStreamingEvent, etc.)
        """
        # Initialize A2A client and get agent card
        a2a_client = await self.create_a2a_client(agent_card)

        # Check agent capability and route to appropriate method
        if self.has_streaming_capability(agent_card):
            logger.info(f"a2a_service: Agent supports streaming: {agent_card.url}")
            async for event in self.send_message_streaming(agent_card, message):
                yield event

        else:
            logger.info(f"a2a_service: Agent doesn't support streaming, using sync: {agent_card.url}")
            try:
                event = await self.send_message_sync(agent_card.url, message, a2a_client)
                yield event
                # Wrap sync response as task_update event for consistency
                # yield TaskUpdateStreamingEvent(
                #     task_id=task_data.get("id"),
                #     state="completed",
                #     task=task_data,
                #     agent_url=agent_card.url,
                # )
                
            except Exception as e:
                yield A2AServiceError(
                    error=str(e),
                    agent_url=agent_card.url,
                )

    async def dry_send_message(
        self, a2a_client: A2AClient, aegnt_card: AgentCard, message_text: str
    ) -> InsepectionCenterConnectionValidationResponse:
        message = Message(
            role=Role.user,
            parts=[TextPart(text=str(message_text))],  # type: ignore[list-item]
            messageId=str(uuid4()),
            contextId=str(uuid4()),
        )

        payload = MessageSendParams(
            message=message,
            configuration=MessageSendConfiguration(acceptedOutputModes=["text/plain"]),
        )

        supports_streaming = (
            hasattr(aegnt_card.capabilities, "streaming")
            and aegnt_card.capabilities.streaming is True
        )

        try:
            if supports_streaming:
                stream_request = SendStreamingMessageRequest(
                    id=str(uuid4()),
                    method="message/stream",
                    jsonrpc="2.0",
                    params=payload,
                )
                response_stream = a2a_client.send_message_streaming(stream_request)

                async for stream_result in response_stream:
                    inspection_center_response = await self.validate_a2a_response(
                        stream_result
                    )
                    inspection_center_response.agent_url = aegnt_card.url
                    inspection_center_response.agent_card = aegnt_card
                return inspection_center_response
            else:
                send_message_request = SendMessageRequest(
                    id=str(uuid4()),
                    method="message/send",
                    jsonrpc="2.0",
                    params=payload,
                )
                send_result = await a2a_client.send_message(send_message_request)
                inspection_center_response = await self.validate_a2a_response(
                    send_result
                )
                inspection_center_response.agent_url = aegnt_card.url
                inspection_center_response.agent_card = aegnt_card
                return inspection_center_response

        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            return InsepectionCenterConnectionValidationResponse(
                agent_url=aegnt_card.url,
                agent_card=aegnt_card,
                result=[f"Failed to send message: {e}"],
                is_valid=False,
                status_code=500,
            )

    async def validate_a2a_response(
        self, result: SendMessageResponse | SendStreamingMessageResponse
    ) -> InsepectionCenterConnectionValidationResponse:
        """Validate a response from the A2A client."""
        if isinstance(result.root, JSONRPCErrorResponse):
            error_data = result.root.error.model_dump(exclude_none=True)

            return InsepectionCenterConnectionValidationResponse(
                agent_url="", result=[str(error_data)], is_valid=False, status_code=500
            )

        # Success case
        response_data = result.root.result

        logger.info(f"validate_a2a_response: response_data: {response_data}")

        response_data = response_data.model_dump(exclude_none=True)

        validation_errors = self.validate_message(response_data)

        return InsepectionCenterConnectionValidationResponse(
            agent_url="", result=validation_errors, is_valid=True, status_code=200
        )

    def validate_message(self, data: dict[str, Any]) -> list[str]:
        """Validate an incoming message from the agent based on its kind."""
        if "kind" not in data:
            return ["Response from agent is missing required 'kind' field."]

        kind = data.get("kind")
        validators = {
            "task": self._validate_task,
            "status-update": self._validate_status_update,
            "artifact-update": self._validate_artifact_update,
            "message": self._validate_message,
        }

        validator = validators.get(str(kind))
        if validator:
            return validator(data)

        return [f"Unknown message kind received: '{kind}'."]

    def _validate_task(self, data: dict[str, Any]) -> list[str]:
        errors = []
        if "id" not in data:
            errors.append("Task object missing required field: 'id'.")
        if "status" not in data or "state" not in data.get("status", {}):
            errors.append("Task object missing required field: 'status.state'.")
        return errors

    def _validate_status_update(self, data: dict[str, Any]) -> list[str]:
        errors = []
        if "status" not in data or "state" not in data.get("status", {}):
            errors.append("StatusUpdate object missing required field: 'status.state'.")
        return errors

    def _validate_artifact_update(self, data: dict[str, Any]) -> list[str]:
        errors = []
        if "artifact" not in data:
            errors.append("ArtifactUpdate object missing required field: 'artifact'.")
        elif (
            "parts" not in data.get("artifact", {})
            or not isinstance(data.get("artifact", {}).get("parts"), list)
            or not data.get("artifact", {}).get("parts")
        ):
            errors.append("Artifact object must have a non-empty 'parts' array.")
        return errors

    def _validate_message(self, data: dict[str, Any]) -> list[str]:
        errors = []
        if (
            "parts" not in data
            or not isinstance(data.get("parts"), list)
            or not data.get("parts")
        ):
            errors.append("Message object must have a non-empty 'parts' array.")
        if "role" not in data or data.get("role") != "agent":
            errors.append("Message from agent must have 'role' set to 'agent'.")
        return errors

    # # for orchestration center (non-streaming - legacy)
    # async def send_message_to_agent(
    #     self, agent_url: str, message: Message
    # ) -> SendMessageResponse | SendStreamingMessageResponse:
    #     """
    #     Send message to agent (non-streaming or buffered streaming).
        
    #     Note: This method buffers streaming responses. For true passthrough
    #     streaming, use send_message_to_agent_streaming() instead.
    #     """
    #     if not agent_url:
    #         raise IllgalParameterError()

    #     try:
    #         httpx_client = httpx.AsyncClient(timeout=600.0)
    #         card_resolver = A2ACardResolver(httpx_client, str(agent_url))
    #         card = await card_resolver.get_agent_card()
    #         a2a_client = A2AClient(httpx_client, agent_card=card)

    #     except Exception as e:
    #         logger.error(f"Failed to initialize a2a client: {e}", exc_info=True)
    #         raise A2AServiceError() from e

    #     payload = MessageSendParams(
    #         message=message,
    #         configuration=MessageSendConfiguration(acceptedOutputModes=["text/plain"]),
    #     )

    #     supports_streaming = (
    #         hasattr(card.capabilities, "streaming")
    #         and card.capabilities.streaming is True
    #     )

    #     try:
    #         if supports_streaming:
    #             stream_request = SendStreamingMessageRequest(
    #                 id=str(uuid4()),
    #                 method="message/stream",
    #                 jsonrpc="2.0",
    #                 params=payload,
    #             )

    #             response_stream = a2a_client.send_message_streaming(stream_request)
    #             last_result = None
    #             async for response in response_stream:
    #                 if isinstance(response.root, JSONRPCErrorResponse):
    #                     logger.error(f"a2a_service: error: {response.root.error}")
    #                     raise A2AServiceError()
    #                 last_result = response

    #             if last_result is None:
    #                 raise A2AServiceError("No response received from streaming")
    #             logger.info(f"a2a_service: last_result: {last_result}")
    #             return last_result
    #         else:
    #             send_message_request = SendMessageRequest(
    #                 id=str(uuid4()),
    #                 method="message/send",
    #                 jsonrpc="2.0",
    #                 params=payload,
    #             )
    #             response = await a2a_client.send_message(send_message_request)
    #             return response

    #     except Exception as e:
    #         logger.error(f"Failed to send message: {e}")
    #         raise A2AServiceError() from e

    # async def send_message_to_agent_streaming(
    #     self, agent_url: str, message: Message
    # ) -> AsyncGenerator[dict[str, Any], None]:
    #     """
    #     Send message to agent with TRUE passthrough streaming.
        
    #     DEPRECATED: Use send_message() instead for automatic capability detection.
        
    #     This method acts as a transparent passthrough - whatever the agent sends
    #     (whether immediately or after processing), we forward it immediately to
    #     the caller. No buffering.
        
    #     Key behavior:
    #     - If agent streams immediately → frontend gets tokens in real-time
    #     - If agent buffers then streams → frontend waits, then gets stream
    #     - If agent sends one response → frontend gets it immediately
        
    #     The server is TRANSPARENT - agent behavior determines latency.
        
    #     Args:
    #         agent_url: URL of the target agent
    #         message: A2A Message to send
            
    #     Yields:
    #         Dict events in our internal format:
    #         - type: "token" | "agent_message" | "task_update" | "error"
    #         - content: The actual data
    #         - metadata: Additional context (agent_id, message_id, etc.)
            
    #     Example:
    #         async for event in a2a_service.send_message_to_agent_streaming(url, msg):
    #             if event["type"] == "token":
    #                 # Forward immediately to frontend
    #                 yield event
    #     """
    #     # Delegate to new send_message() method which handles all the logic
    #     async for event in self.send_message(agent_url, message):
    #         yield event

    async def process_a2a_response(
        self, response: SendMessageResponse | SendStreamingMessageResponse
    ) -> Any:
        if isinstance(response.root, JSONRPCErrorResponse):
            raise A2AServiceError()

        # Success case
        logger.info(f"process_a2a_response: response: {response}")

        response_data = response.root.result

        logger.info(f"process_a2a_response: response_data: {response_data}")

        return response_data

    async def process_a2a_response_dict(self, response_dict: dict[str, Any]) -> Any:
        """
        Process a response that's already been converted to a dictionary.
        
        This is useful when we've received streaming events and need to process
        the final task_update event which comes as a dict.
        
        Args:
            response_dict: A dictionary representation of an A2A response
            
        Returns:
            The processed response data (Task, Message, etc.)
        """
        # The dict should have a 'kind' field that tells us the type
        # We just need to return it as-is since it's already processed
        return response_dict


a2a_service = A2AService()
