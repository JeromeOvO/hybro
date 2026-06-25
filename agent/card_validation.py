from __future__ import annotations

from typing import Any

_REQUIRED_FIELDS = frozenset(
    [
        "name",
        "description",
        "url",
        "version",
        "capabilities",
        "defaultInputModes",
        "defaultOutputModes",
        "skills",
    ]
)
_MODE_FIELDS = ["defaultInputModes", "defaultOutputModes"]


async def validate_agent_card_data(card_data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    errors.extend(_missing_required_field_errors(card_data))
    errors.extend(_url_errors(card_data))
    errors.extend(_capability_errors(card_data))
    errors.extend(_mode_errors(card_data))
    errors.extend(_skill_errors(card_data))
    return errors


def _missing_required_field_errors(card_data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field in _REQUIRED_FIELDS:
        if field not in card_data:
            errors.append(f"Required field is missing: '{field}'.")
    return errors


def _url_errors(card_data: dict[str, Any]) -> list[str]:
    if "url" in card_data and not (
        card_data["url"].startswith("http://")
        or card_data["url"].startswith("https://")
    ):
        return [
            "Field 'url' must be an absolute URL starting with http:// or https://."
        ]
    return []


def _capability_errors(card_data: dict[str, Any]) -> list[str]:
    if "capabilities" in card_data and not isinstance(card_data["capabilities"], dict):
        return ["Field 'capabilities' must be an object."]
    return []


def _mode_errors(card_data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field in _MODE_FIELDS:
        if field in card_data:
            if not isinstance(card_data[field], list):
                errors.append(f"Field '{field}' must be an array of strings.")
            elif not all(isinstance(item, str) for item in card_data[field]):
                errors.append(f"All items in '{field}' must be strings.")
    return errors


def _skill_errors(card_data: dict[str, Any]) -> list[str]:
    if "skills" in card_data:
        if not isinstance(card_data["skills"], list):
            return ["Field 'skills' must be an array of AgentSkill objects."]
        if not card_data["skills"]:
            return [
                "Field 'skills' array is empty. Agent must have at least one skill if it performs actions."
            ]
    return []
