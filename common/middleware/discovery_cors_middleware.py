"""
CORS Middleware for Discovery API

Applies permissive CORS settings specifically for the Discovery API endpoints
to allow external access from any origin.
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from config.settings import settings


class DiscoveryCORSMiddleware(BaseHTTPMiddleware):
    """
    Middleware that adds permissive CORS headers for Discovery and Gateway API endpoints.
    
    Applies to paths starting with {api_prefix}/discovery or {api_prefix}/gateway.
    Allows all origins, methods, and headers for external API access.
    """

    async def dispatch(self, request: Request, call_next):
        discovery_path_prefix = f"{settings.api_prefix}/discovery"
        gateway_path_prefix = f"{settings.api_prefix}/gateway"
        is_external_api = (
            request.url.path.startswith(discovery_path_prefix)
            or request.url.path.startswith(gateway_path_prefix)
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

