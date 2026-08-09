from __future__ import annotations

from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel


class EventModelRegistry:
    def __init__(self) -> None:
        self._models: dict[str, type[BaseModel]] = {}
        self._frozen = False

    @property
    def frozen(self) -> bool:
        return self._frozen

    def register(self, event_type: str, model: type[BaseModel]) -> None:
        if self._frozen:
            raise RuntimeError("Event model registry is frozen")
        if not event_type:
            raise ValueError("event_type must be non-empty")
        if not isinstance(model, type) or not issubclass(model, BaseModel):
            raise TypeError("Internal event models must be BaseModel subclasses")
        event_type_field = model.model_fields.get("event_type")
        if event_type_field is None or event_type_field.default != event_type:
            raise ValueError(
                f"Internal event model default does not match {event_type}"
            )
        annotation = event_type_field.annotation
        if get_origin(annotation) is not Literal or event_type not in get_args(
            annotation
        ):
            raise ValueError(
                f"Internal event model discriminator does not match {event_type}"
            )
        existing = self._models.get(event_type)
        if existing is not None and existing is not model:
            raise ValueError(f"event_type already registered: {event_type}")
        self._models[event_type] = model

    def freeze(self) -> None:
        self._frozen = True

    def deserialize(self, event_type: str, payload: Any) -> BaseModel:
        model = self._models.get(event_type)
        if model is None:
            raise ValueError(f"Unregistered internal event type: {event_type}")
        return model.model_validate(payload)

    def event_type_for(self, event: BaseModel) -> str:
        event_type = getattr(event, "event_type", None)
        if not isinstance(event_type, str) or not event_type:
            raise ValueError("Internal events must expose a non-empty event_type")
        model = self._models.get(event_type)
        if model is None:
            raise ValueError(f"Unregistered internal event type: {event_type}")
        if not isinstance(event, model):
            raise ValueError(f"Internal event model does not match {event_type}")
        return event_type


__all__ = ["EventModelRegistry"]
