"""Compatibility shim for route declarations moved to api_gateway.routes.platform_gateway_routes."""

import sys as _sys

from api_gateway.routes import platform_gateway_routes as _routes

_sys.modules[__name__] = _routes
