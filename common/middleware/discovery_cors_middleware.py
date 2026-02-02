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
    Middleware that adds permissive CORS headers for Discovery API endpoints.
    
    Only applies to paths starting with {api_prefix}/discovery.
    Allows all origins, methods, and headers for external API access.
    """

    async def dispatch(self, request: Request, call_next):
        # Check if this is a Discovery API request
        discovery_path_prefix = f"{settings.api_prefix}/discovery"
        is_discovery_api = request.url.path.startswith(discovery_path_prefix)
        
        # Handle preflight OPTIONS requests for Discovery API
        if is_discovery_api and request.method == "OPTIONS":
            response = Response()
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "*"
            response.headers["Access-Control-Max-Age"] = "3600"
            return response
        
        # Process the request
        response = await call_next(request)
        
        # Add CORS headers to Discovery API responses
        if is_discovery_api:
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "*"
        
        return response

