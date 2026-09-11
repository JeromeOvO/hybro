"""A2A 1.0 SDK object factories for tests.

A2A 1.0 moved the SDK onto protobuf types: ``AgentCard`` no longer has a
top-level ``url`` (endpoints live in ``supported_interfaces``), enums are
``SCREAMING_SNAKE_CASE``, and ``Part`` is a single unified message. Tests build
protocol objects through these helpers so they stay readable and so a future
protocol revision changes one file instead of every test.
"""

from __future__ import annotations

from typing import Any

from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Part,
    Role,
)

JSONRPC_BINDING = "JSONRPC"
PROTOCOL_VERSION_1_0 = "1.0"
PROTOCOL_VERSION_0_3 = "0.3"


def make_agent_card(
    *,
    name: str = "TestAgent",
    description: str = "A test agent",
    url: str = "https://test-agent.example.com",
    version: str = "1.0.0",
    capabilities: dict[str, Any] | None = None,
    skills: list[dict[str, Any]] | None = None,
    default_input_modes: list[str] | None = None,
    default_output_modes: list[str] | None = None,
    protocol_version: str = PROTOCOL_VERSION_1_0,
    protocol_binding: str = JSONRPC_BINDING,
) -> AgentCard:
    """Build a 1.0 agent card with one advertised JSON-RPC interface."""
    card = AgentCard(
        name=name,
        description=description,
        version=version,
        capabilities=AgentCapabilities(**(capabilities or {})),
        default_input_modes=list(default_input_modes or ["text"]),
        default_output_modes=list(default_output_modes or ["text"]),
    )
    card.supported_interfaces.append(
        AgentInterface(
            url=url,
            protocol_binding=protocol_binding,
            protocol_version=protocol_version,
        )
    )
    for skill in skills or []:
        card.skills.append(AgentSkill(**skill))
    return card


def make_skill(**overrides: Any) -> AgentSkill:
    data: dict[str, Any] = {
        "id": "test-skill",
        "name": "Test Skill",
        "description": "A test skill",
        "tags": ["test"],
    }
    data.update(overrides)
    return AgentSkill(**data)


def make_legacy_card(
    *,
    name: str = "LegacyAgent",
    url: str = "https://legacy-agent.example.com",
    version: str = "0.0.1",
    protocol_version: str = PROTOCOL_VERSION_0_3,
    **overrides: Any,
) -> AgentCard:
    """Build a card that advertises a pre-1.0 interface.

    The card object itself is 1.0 shaped; only the advertised protocol version
    is old, which is what selects the SDK's compatibility transport.
    """
    return make_agent_card(
        name=name,
        url=url,
        version=version,
        protocol_version=protocol_version,
        **overrides,
    )


def text_part(text: str) -> Part:
    part = Part()
    part.text = text
    return part


def user_message(*parts: Part, message_id: str = "msg-1") -> Any:
    """Build a user message from explicit parts."""
    from a2a.types import Message

    message = Message(message_id=message_id, role=Role.ROLE_USER)
    message.parts.extend(parts)
    return message


__all__ = [
    "JSONRPC_BINDING",
    "PROTOCOL_VERSION_0_3",
    "PROTOCOL_VERSION_1_0",
    "make_agent_card",
    "make_legacy_card",
    "make_skill",
    "text_part",
    "user_message",
]
