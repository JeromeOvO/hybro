"""SDK-free card normalization and SDK card construction.

Card data reaches the adapter in several shapes: an internal ``AgentCard``, a
raw dict from persistence, or a card fetched from the wire. All of them are
converted to a plain dict here and then parsed by the SDK's own card parser.

Hybro speaks A2A 1.0 only. The SDK parser still maps a pre-1.0 top-level
``url`` onto an interface whose ``protocolVersion`` defaults to ``0.3.0``, and
the SDK client would select a 0.3 compatibility transport for such an
interface. Cards advertising any pre-1.0 interface are therefore rejected here,
before a client is built.
"""

from __future__ import annotations

from typing import Any

from a2a.client.card_resolver import parse_agent_card
from a2a.types import AgentCard

from common.errors import UnsupportedAgentProtocolError

# A card reached through Hybro always speaks JSON-RPC until the wire says
# otherwise, so an unspecified binding gets the JSON-RPC default.
_DEFAULT_BINDING = "JSONRPC"


def agent_card_dict(agent_card_data: Any) -> dict[str, Any]:
    """Return plain camelCase card data from an internal model or dict."""
    return _model_data(agent_card_data)


def build_agent_card(agent_card_data: Any) -> AgentCard:
    """Build an SDK agent card from internal models, dicts, or wire JSON.

    A card with no callable interface is rejected: every card in Hybro exists to
    be called, and a card that advertises no endpoint cannot be. This also keeps
    a truncated or malformed card from being registered as a working agent. A
    card advertising a pre-1.0 interface is rejected as unsupported.
    """
    if isinstance(agent_card_data, AgentCard):
        ensure_supported_protocol(agent_card_data)
        return agent_card_data
    data = _model_data(agent_card_data)
    if not data:
        raise ValueError("agent card data is empty")
    card = parse_agent_card(data)
    if not _interface_urls(card):
        raise ValueError("agent card advertises no callable interface")
    ensure_supported_protocol(card)
    return card


def ensure_supported_protocol(card: AgentCard) -> None:
    """Reject a card that advertises any pre-1.0 interface.

    The SDK client may pick any advertised interface, including a 0.3 one when
    a 1.0 interface is also offered, so a single legacy interface is enough to
    reject the card. Empty or unparseable versions are left to the SDK.
    """
    for interface in card.supported_interfaces:
        major = _major_version(interface.protocol_version)
        if major is not None and major < 1:
            raise UnsupportedAgentProtocolError(
                f"agent card advertises A2A protocol version "
                f"'{interface.protocol_version}'; Hybro requires A2A 1.0 or later"
            )


def _major_version(version: str) -> int | None:
    head = version.strip().lstrip("vV").split(".", 1)[0]
    return int(head) if head.isdigit() else None


def _interface_urls(card: AgentCard) -> list[str]:
    return [interface.url for interface in card.supported_interfaces if interface.url]


def _model_data(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        # The SDK's compatibility parser mutates nested skill/security mappings.
        # Copy containers so frozen DTOs work without altering registered cards.
        return _mutable_json(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    return {}


def _mutable_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _mutable_json(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_mutable_json(item) for item in value]
    return value


__all__ = ["agent_card_dict", "build_agent_card", "ensure_supported_protocol"]
