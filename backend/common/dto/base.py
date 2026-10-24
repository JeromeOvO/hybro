from collections.abc import Mapping
from copy import deepcopy as _deepcopy_value
from typing import Any, Self

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


class FrozenDict(dict):
    """Dict variant that rejects in-place mutation but supports copying."""

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

    def __copy__(self) -> "FrozenDict":
        return FrozenDict(self)

    def __deepcopy__(self, memo: dict) -> "FrozenDict":
        existing = memo.get(id(self))
        if existing is not None:
            return existing
        result = FrozenDict(
            (_deepcopy_value(key, memo), _deepcopy_value(value, memo))
            for key, value in self.items()
        )
        memo[id(self)] = result
        return result


class FrozenList(list):
    """List variant that rejects in-place mutation but supports copying."""

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

    def __copy__(self) -> "FrozenList":
        return FrozenList(self)

    def __deepcopy__(self, memo: dict) -> "FrozenList":
        existing = memo.get(id(self))
        if existing is not None:
            return existing
        result = FrozenList(_deepcopy_value(item, memo) for item in self)
        memo[id(self)] = result
        return result


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


STORED_CONVERSATION_ID_KEY = "room_id"
_CONVERSATION_ID_CHOICES = AliasChoices("conversation_id", STORED_CONVERSATION_ID_KEY)


def _default_args(default: Any, kwargs: dict[str, Any]) -> tuple[Any, ...]:
    return () if "default_factory" in kwargs else (default,)


def stored_conversation_id(default: Any = ..., **kwargs: Any) -> Any:
    """Field for ``conversation_id`` persisted under the legacy ``room_id`` key.

    Mongo documents and retained internal transport payloads keep the stored key
    ``room_id``. The owning model must set ``serialize_by_alias=True`` so every
    ``model_dump`` writes ``room_id``; validation accepts either name.
    """

    return Field(
        *_default_args(default, kwargs),
        validation_alias=_CONVERSATION_ID_CHOICES,
        serialization_alias=STORED_CONVERSATION_ID_KEY,
        **kwargs,
    )


def accepts_stored_conversation_id(default: Any = ..., **kwargs: Any) -> Any:
    """Field for ``conversation_id`` that also reads the stored ``room_id`` key.

    Use it on models that are both persisted and returned on the wire: they
    serialize as ``conversation_id``, and their repositories rename the key with
    ``to_stored_conversation_keys`` before writing to Mongo.
    """

    return Field(
        *_default_args(default, kwargs),
        validation_alias=_CONVERSATION_ID_CHOICES,
        **kwargs,
    )


def to_stored_conversation_keys(document: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy whose ``conversation_id`` key is renamed to ``room_id``."""

    return {
        (STORED_CONVERSATION_ID_KEY if key == "conversation_id" else key): value
        for key, value in document.items()
    }


__all__ = [
    "STORED_CONVERSATION_ID_KEY",
    "accepts_stored_conversation_id",
    "to_stored_conversation_keys",
    "FrozenDTO",
    "FrozenDict",
    "FrozenList",
    "stored_conversation_id",
]
