from __future__ import annotations

from typing import Any


def sdk_agent_card_data(agent_card_data: Any) -> dict[str, Any]:
    """Return SDK-compatible AgentCard data from dicts or internal models."""
    if isinstance(agent_card_data, dict):
        data = dict(agent_card_data)
    elif hasattr(agent_card_data, "model_dump"):
        data = agent_card_data.model_dump(mode="json", by_alias=True)
    else:
        data = {}

    if data.get("description") is None:
        data["description"] = ""
    if data.get("defaultInputModes") is None:
        data["defaultInputModes"] = ["text/plain"]
    if data.get("defaultOutputModes") is None:
        data["defaultOutputModes"] = ["text/plain"]

    normalized_skills = []
    for skill in data.get("skills") or []:
        skill_data = dict(skill)
        if skill_data.get("description") is None:
            skill_data["description"] = ""
        if skill_data.get("tags") is None:
            skill_data["tags"] = []
        normalized_skills.append(skill_data)
    data["skills"] = normalized_skills
    return data


__all__ = ["sdk_agent_card_data"]
