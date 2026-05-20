from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class A2ATaskReader(Protocol):
    async def get(self, *args: Any, **kwargs: Any) -> Any: ...
    async def list(self, *args: Any, **kwargs: Any) -> Any: ...


@runtime_checkable
class AgentGroupStore(Protocol):
    async def get(self, *args: Any, **kwargs: Any) -> Any: ...
    async def list(self, *args: Any, **kwargs: Any) -> Any: ...
    async def save(self, *args: Any, **kwargs: Any) -> Any: ...


__all__ = ["A2ATaskReader", "AgentGroupStore"]
