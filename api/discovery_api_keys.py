"""Compatibility shim for route declarations moved to api_gateway.routes.discovery_api_key_routes."""

import sys as _sys

from api_gateway.routes import discovery_api_key_routes as _routes

_sys.modules[__name__] = _routes
