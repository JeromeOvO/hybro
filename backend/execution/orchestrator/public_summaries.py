"""Deny-by-default public Tool/Agent summary projection registry."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from .models import FrozenToolCatalogSnapshot
from .public_text import sanitize_public_text

type SafeScalar = str | int | float | bool | None
type SafeSummary = dict[str, SafeScalar]
type InputSummaryBuilder = Callable[[Mapping[str, Any]], Mapping[str, SafeScalar]]
type ResultSummaryBuilder = Callable[[Any], str]


@dataclass(frozen=True, slots=True)
class SummaryBuilders:
    input: InputSummaryBuilder
    result: ResultSummaryBuilder


class PublicSummaryRegistry:
    """Explicit public projections shared by Tool Trace and Agent Cards.

    Unknown tools return empty summaries. Builders cannot return nested values,
    and failures also close to the empty fallback rather than exposing raw data.
    """

    def __init__(
        self,
        *,
        max_text: int = 1_000,
        max_fields: int = 12,
        secret_values: tuple[str, ...] = (),
    ) -> None:
        self._builders: dict[tuple[str | None, str | None, str], SummaryBuilders] = {}
        self._max_text = max_text
        self._max_fields = max_fields
        self._secret_values = secret_values

    def register(
        self,
        tool_name: str,
        *,
        input_builder: InputSummaryBuilder,
        result_builder: ResultSummaryBuilder,
    ) -> None:
        key = tool_name.strip()
        if not key:
            raise ValueError("tool_name is required")
        registry_key = (None, None, key)
        if registry_key in self._builders:
            raise ValueError(f"public summary builder already registered: {key}")
        self._builders[registry_key] = SummaryBuilders(input_builder, result_builder)

    def register_catalog(self, catalog: FrozenToolCatalogSnapshot) -> None:
        """Register conservative builders only for tools in a frozen catalog.

        ``task`` is the sole built-in public argument. Additional scalar fields
        require an explicit ``x-hybro-public-summary`` marker in the frozen JSON
        schema. Result/progress text is accepted only after the Kernel has
        normalized it to a string; unknown tool names remain deny-by-default.
        """
        for entry in catalog.entries:
            tool_name = entry.definition.name.strip()
            if not tool_name:
                continue
            schema = entry.definition.input_schema
            schema_digest = _schema_digest(schema)
            registry_key = (catalog.catalog_id, schema_digest, tool_name)
            if registry_key in self._builders:
                continue
            properties = schema.get("properties") if isinstance(schema, Mapping) else {}
            public_fields: tuple[str, ...] = tuple(
                key
                for key, value in (
                    properties.items() if isinstance(properties, Mapping) else ()
                )
                if isinstance(key, str)
                and isinstance(value, Mapping)
                and (key == "task" or value.get("x-hybro-public-summary") is True)
                and value.get("type") in {"string", "integer", "number", "boolean"}
            )

            def input_builder(
                arguments: Mapping[str, Any], *, fields: tuple[str, ...] = public_fields
            ) -> Mapping[str, SafeScalar]:
                return {
                    key: value
                    for key in fields
                    if isinstance(
                        (value := arguments.get(key)),
                        str | int | float | bool | type(None),
                    )
                }

            def result_builder(value: Any) -> str:
                return value if isinstance(value, str) else ""

            self._builders[registry_key] = SummaryBuilders(
                input_builder,
                result_builder,
            )

    def input_summary(
        self,
        tool_name: str,
        arguments: Any,
        *,
        catalog: FrozenToolCatalogSnapshot | None = None,
    ) -> SafeSummary:
        builders = self._builders.get(_builder_key(catalog, tool_name))
        if builders is None or not isinstance(arguments, Mapping):
            return {}
        try:
            raw = builders.input(arguments)
        except Exception:
            return {}
        if not isinstance(raw, Mapping):
            return {}
        result: SafeSummary = {}
        for key, value in list(raw.items())[: self._max_fields]:
            if (
                not isinstance(key, str)
                or not key
                or not isinstance(value, str | int | float | bool | type(None))
            ):
                return {}
            result[key[:80]] = (
                sanitize_public_text(
                    value[: self._max_text], secret_values=self._secret_values
                )
                if isinstance(value, str)
                else value
            )
        return result

    def result_summary(
        self,
        tool_name: str,
        value: Any,
        *,
        catalog: FrozenToolCatalogSnapshot | None = None,
    ) -> str:
        builders = self._builders.get(_builder_key(catalog, tool_name))
        if builders is None:
            return ""
        try:
            projected = builders.result(value)
        except Exception:
            return ""
        if not isinstance(projected, str):
            return ""
        return sanitize_public_text(
            projected[: self._max_text], secret_values=self._secret_values
        )


def _schema_digest(schema: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        schema,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return sha256(canonical.encode()).hexdigest()


def _builder_key(
    catalog: FrozenToolCatalogSnapshot | None,
    tool_name: str,
) -> tuple[str | None, str | None, str]:
    if catalog is None:
        return (None, None, tool_name)
    entry = next(
        (
            candidate
            for candidate in catalog.entries
            if candidate.definition.name.strip() == tool_name
        ),
        None,
    )
    if entry is None:
        return (catalog.catalog_id, None, tool_name)
    return (
        catalog.catalog_id,
        _schema_digest(entry.definition.input_schema),
        tool_name,
    )


__all__ = [
    "InputSummaryBuilder",
    "PublicSummaryRegistry",
    "ResultSummaryBuilder",
    "SafeSummary",
    "SummaryBuilders",
]
