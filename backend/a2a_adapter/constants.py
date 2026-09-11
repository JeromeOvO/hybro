"""Protocol constants kept behind the adapter boundary.

The current well-known card path comes from the SDK so it tracks the protocol
revision in use. The previous path is a wire literal for agents that predate
the rename; it is data, not SDK API, so it is declared here rather than imported.
"""

from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH

PREV_AGENT_CARD_WELL_KNOWN_PATH = "/.well-known/agent.json"

# Protocol binding labels used when selecting an interface from a card. Order is
# the client's preference: JSON-RPC preserves the binding Hybro has always used,
# and HTTP+JSON is accepted so an agent that only publishes the REST binding can
# still be called. The SDK's own transport carries both, so the choice does not
# change how responses reach the adapter.
JSONRPC_BINDING = "JSONRPC"
HTTP_JSON_BINDING = "HTTP+JSON"
SUPPORTED_BINDINGS = [JSONRPC_BINDING, HTTP_JSON_BINDING]

__all__ = [
    "AGENT_CARD_WELL_KNOWN_PATH",
    "HTTP_JSON_BINDING",
    "JSONRPC_BINDING",
    "PREV_AGENT_CARD_WELL_KNOWN_PATH",
    "SUPPORTED_BINDINGS",
]
