"""Compatibility shim for route declarations moved to api_gateway.routes.a2a_task_routes."""

import sys as _sys

from api_gateway.routes import a2a_task_routes as _routes

_sys.modules[__name__] = _routes
