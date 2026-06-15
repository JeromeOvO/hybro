"""Compatibility shim for route declarations moved to api_gateway.viewsets.base."""

import sys as _sys

from api_gateway.viewsets import base as _base_viewset

_sys.modules[__name__] = _base_viewset
