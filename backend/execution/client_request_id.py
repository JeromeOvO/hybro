from __future__ import annotations


class SSEClientRequestIdResolver:
    def __init__(self, resolver) -> None:
        self._resolver = resolver

    async def resolve_client_request_id(
        self,
        message_id: str | None,
        provided_client_request_id: str | None,
    ) -> str | None:
        if provided_client_request_id:
            return provided_client_request_id
        if not message_id:
            return None
        return await self._resolver.resolve_client_request_id_for_message_id(message_id)
