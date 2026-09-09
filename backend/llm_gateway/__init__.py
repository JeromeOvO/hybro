"""Public gateway exports, loaded on demand so host CLI avoids app settings."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .gateway import LLMGatewayImpl
    from .model_registry import ModelRegistryImpl

__all__ = ["LLMGatewayImpl", "ModelRegistryImpl"]


def __getattr__(name: str) -> type[LLMGatewayImpl] | type[ModelRegistryImpl]:
    if name == "LLMGatewayImpl":
        from .gateway import LLMGatewayImpl

        return LLMGatewayImpl
    if name == "ModelRegistryImpl":
        from .model_registry import ModelRegistryImpl

        return ModelRegistryImpl
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
