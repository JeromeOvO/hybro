"""Compatibility shim for route declarations moved to api_gateway.viewsets.agent."""

import sys as _sys

from api_gateway.viewsets import agent as _agent_viewset

_sys.modules[__name__] = _agent_viewset
