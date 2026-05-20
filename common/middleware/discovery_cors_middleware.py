"""
CORS Middleware for Discovery API

Applies permissive CORS settings specifically for the Discovery API endpoints
to allow external access from any origin.
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class DiscoveryCORSMiddleware(BaseHTTPMiddleware):
    """
    Middleware that adds permissive CORS headers for Discovery, Gateway, and Relay API endpoints.
    
    Applies to paths starting with {api_prefix}/discovery, {api_prefix}/gateway,
    or {api_prefix}/relay.
    Allows all origins, methods, and headers for external API access.
    """

    def __init__(self, app, *, api_prefix: str = "/api/v1") -> None:
        super().__init__(app)
        self._discovery_path_prefix = f"{api_prefix}/discovery"
        self._gateway_path_prefix = f"{api_prefix}/gateway"
        self._relay_path_prefix = f"{api_prefix}/relay"

    async def dispatch(self, request: Request, call_next):
        is_external_api = (
            request.url.path.startswith(self._discovery_path_prefix)
            or request.url.path.startswith(self._gateway_path_prefix)
            or request.url.path.startswith(self._relay_path_prefix)
        )
        
        if is_external_api and request.method == "OPTIONS":
            response = Response()
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "*"
            response.headers["Access-Control-Max-Age"] = "3600"
            return response
        
        response = await call_next(request)
        
        if is_external_api:
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "*"
        
        return response
