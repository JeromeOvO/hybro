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
    SendStreamingMessageRequest,
    SendStreamingMessageResponse,
    TextPart,
)

from common.utils.logger import get_logger
from models.error import A2AServiceError, IllgalParameterError
from models.response import InsepectionCenterConnectionValidationResponse

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
            raise A2AServiceError()

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
            raise A2AServiceError()

    # for inspection center
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

    # for orchestration center
    async def send_message_to_agent(
        self, agent_url: str, message: Message
    ) -> SendMessageResponse | SendStreamingMessageResponse:
        if not agent_url:
            raise IllgalParameterError()

        try:
            httpx_client = httpx.AsyncClient(timeout=600.0)
            card_resolver = A2ACardResolver(httpx_client, str(agent_url))
            card = await card_resolver.get_agent_card()
            a2a_client = A2AClient(httpx_client, agent_card=card)

        except Exception as e:
            logger.error(f"Failed to initialize a2a client: {e}", exc_info=True)
            raise A2AServiceError()

        payload = MessageSendParams(
            message=message,
            configuration=MessageSendConfiguration(acceptedOutputModes=["text/plain"]),
        )

        supports_streaming = (
            hasattr(card.capabilities, "streaming")
            and card.capabilities.streaming is True
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
                last_result = None
                async for response in response_stream:
                    if isinstance(response.root, JSONRPCErrorResponse):
                        logger.error(f"a2a_service: error: {response.root.error}")
                        raise A2AServiceError()
                    last_result = response

                if last_result is None:
                    raise A2AServiceError("No response received from streaming")
                logger.info(f"a2a_service: last_result: {last_result}")
                return last_result
            else:
                send_message_request = SendMessageRequest(
                    id=str(uuid4()),
                    method="message/send",
                    jsonrpc="2.0",
                    params=payload,
                )
                response = await a2a_client.send_message(send_message_request)
                return response

        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            raise A2AServiceError()

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


a2a_service = A2AService()
