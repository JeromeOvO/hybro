from __future__ import annotations

from typing import Any

from execution.ports import ProcessingStatusLike


class LegacyProcessingStatusC3Adapter:
    def __init__(self, sse_manager) -> None:
        self._sse_manager = sse_manager

    async def emit_processing_status(
        self,
        *,
        room_id: str,
        status: ProcessingStatusLike,
        message_id: str | None,
        details: dict[str, Any] | str | None = None,
        client_request_id: str | None = None,
        agents: list[dict] | None = None,
    ) -> None:
        status_value = getattr(status, "value", status)
        await self._sse_manager.send_processing_status(
            room_id,
            status_value,
            message_id,
            details=details,
            client_request_id=client_request_id,
            agents=agents,
        )


class SSEClientRequestIdResolver:
    def __init__(self, db_service) -> None:
        self._db_service = db_service

    async def resolve_client_request_id(
        self,
        message_id: str | None,
        provided_client_request_id: str | None,
    ) -> str | None:
        if provided_client_request_id:
            return provided_client_request_id
        if not message_id:
            return None
        return await self._db_service.resolve_client_request_id_for_message_id(
            message_id
        )
