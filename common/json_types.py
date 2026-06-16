from __future__ import annotations

from typing import TypeAlias

JsonScalar: TypeAlias = str | int | float | bool | None  # noqa: UP040
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]  # noqa: UP040
JsonMap: TypeAlias = dict[str, JsonValue]  # noqa: UP040

__all__ = ["JsonMap", "JsonScalar", "JsonValue"]
