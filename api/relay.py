"""Compatibility shim for route declarations moved to api_gateway.routes.relay_routes."""

import sys as _sys

from api_gateway.routes import relay_routes as _routes

_sys.modules[__name__] = _routes
