from infrastructure.relay_streams import RelayStreamService
from hub_runtime_bridge.transport.relay_streams import RelayStreamService as HubRelayStreamService


def test_legacy_relay_stream_import_is_phase8_shim() -> None:
    assert RelayStreamService is HubRelayStreamService
