import json

import httpx

from common.types import A2AClientJSONError, AgentCard


_REQUIRED_AGENT_CARD_FIELDS = (
    "name",
    "description",
    "url",
    "version",
    "capabilities",
    "defaultInputModes",
    "defaultOutputModes",
    "skills",
)


def _validate_agent_card_payload(payload: dict) -> None:
    missing = [field for field in _REQUIRED_AGENT_CARD_FIELDS if field not in payload]
    if missing:
        raise A2AClientJSONError(
            "Agent card missing required field(s): " + ", ".join(missing)
        )


class A2ACardResolver:
    def __init__(self, base_url, agent_card_path="/.well-known/agent.json"):
        self.base_url = base_url.rstrip("/")
        self.agent_card_path = agent_card_path.lstrip("/")

    def get_agent_card(self) -> AgentCard:
        with httpx.Client() as client:
            response = client.get(self.base_url + "/" + self.agent_card_path)
            response.raise_for_status()
            try:
                payload = response.json()
                _validate_agent_card_payload(payload)
                return AgentCard(**payload)
            except json.JSONDecodeError as e:
                raise A2AClientJSONError(str(e)) from e
