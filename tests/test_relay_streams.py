from hub_runtime_bridge.transport.relay_streams import (
    RelayStreamService as HubRelayStreamService,
)
from app_shell.redis_runtime import AppShellRelayStreamService


def test_app_shell_relay_stream_service_uses_hub_runtime_behavior() -> None:
    assert issubclass(AppShellRelayStreamService, HubRelayStreamService)
