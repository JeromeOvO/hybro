from typing import Any, Self

from pydantic import BaseModel, ConfigDict, model_validator


class FrozenDict(dict):
    """Dict variant that rejects in-place mutation."""

    def _immutable(self, *args, **kwargs):
        raise TypeError("FrozenDict is immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable
    __ior__ = _immutable


class FrozenList(list):
    """List variant that rejects in-place mutation."""

    def _immutable(self, *args, **kwargs):
        raise TypeError("FrozenList is immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    append = _immutable
    clear = _immutable
    extend = _immutable
    insert = _immutable
    pop = _immutable
    remove = _immutable
    reverse = _immutable
    sort = _immutable
    __iadd__ = _immutable
    __imul__ = _immutable


def _freeze_value(value: Any) -> Any:
    if isinstance(value, FrozenDict):
        return value
    if isinstance(value, FrozenList):
        return value
    if isinstance(value, dict):
        return FrozenDict((key, _freeze_value(item)) for key, item in value.items())
    if isinstance(value, list):
        return FrozenList(_freeze_value(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, set | frozenset):
        return frozenset(_freeze_value(item) for item in value)
    return value


class FrozenDTO(BaseModel):
    """Base for immutable module-boundary DTOs."""

    model_config = ConfigDict(frozen=True)

    @model_validator(mode="after")
    def freeze_container_fields(self) -> Self:
        for field_name in self.__class__.model_fields:
            object.__setattr__(
                self,
                field_name,
                _freeze_value(getattr(self, field_name)),
            )
        return self


__all__ = ["FrozenDTO", "FrozenDict", "FrozenList"]
