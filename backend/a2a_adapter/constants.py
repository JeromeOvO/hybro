"""Protocol constants kept behind the adapter boundary.

The current well-known card path comes from the SDK so it tracks the protocol
revision in use. The previous path is a wire literal for agents that predate
the rename; it is data, not SDK API, so it is declared here rather than imported.
"""

from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH

PREV_AGENT_CARD_WELL_KNOWN_PATH = "/.well-known/agent.json"

# Protocol binding label used when selecting and pinning an interface.
JSONRPC_BINDING = "JSONRPC"

__all__ = [
    "AGENT_CARD_WELL_KNOWN_PATH",
    "JSONRPC_BINDING",
    "PREV_AGENT_CARD_WELL_KNOWN_PATH",
]
