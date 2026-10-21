"""C2 step 7: the A2A push-notification webhook ingress is retired."""

from __future__ import annotations

import importlib
import inspect
import json
from pathlib import Path

import pytest

from common.config.loader import PRIVATE_FIELDS, REMOVED_FIELDS, Settings
from common.config.runtime_store import RETIRED_SERVICE_KEYS, SERVICE_KEYS
from tests.test_json_runtime_config import configured

BACKEND = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "module",
    [
        "api_gateway.routes.webhook_routes",
        "dal.runtime_store.parts.webhook_tokens",
    ],
)
def test_webhook_modules_are_gone(module):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)


def test_application_serves_no_webhook_route():
    from main import app

    paths = {getattr(route, "path", "") for route in app.routes}

    assert not any("/webhooks" in path for path in paths)
    assert not any("/webhooks" in path for path in app.openapi()["paths"])


def test_routing_has_no_webhook_ingress():
    import execution.orchestrator_routing as routing
    from execution.facade import ExecutionFacade

    assert not hasattr(routing, "WebhookAuthenticationError")
    assert not hasattr(routing, "WebhookTokenVerifier")
    for name in (
        "route_webhook",
        "record_observation",
        "_authenticate_webhook",
        "_resolve_webhook_call",
    ):
        assert not hasattr(routing.DualRuntimeRouter, name), name
    assert (
        "webhook_token_verifier"
        not in inspect.signature(routing.DualRuntimeRouter).parameters
    )
    assert not hasattr(ExecutionFacade, "route_webhook")


def test_webhook_receiver_protocol_is_gone():
    import common.protocols as protocols

    assert not hasattr(protocols, "WebhookReceiver")


def test_task_lifecycle_store_keeps_only_correlation():
    from conversation.repository import ClientRequestIdStore

    public = {
        name
        for name, member in inspect.getmembers(ClientRequestIdStore)
        if callable(member) and not name.startswith("__")
    }

    assert public == {
        "resolve_client_request_id_for_agent_message",
        "resolve_client_request_id_for_message_id",
    }
    for name in (
        "hash_webhook_token",
        "verify_webhook_token",
        "generate_webhook_token",
        "verify_webhook_token_on_message",
        "verify_webhook_token_for_task",
    ):
        assert not hasattr(ClientRequestIdStore, name), name


def test_messages_no_longer_carry_a_webhook_token_hash():
    from common.dto.runtime_store import RuntimeRoomAgentMessage
    from models.room import RoomAgentMessage

    assert "webhook_token_hash" not in RoomAgentMessage.model_fields
    assert "webhook_token_hash" not in RuntimeRoomAgentMessage.model_fields


def test_a2a_adapter_has_no_push_configuration():
    import a2a_adapter.client_facade as client_facade
    from a2a_adapter.runtime_service import A2ARuntimeConfig

    assert "webhook_base_url" not in A2ARuntimeConfig.__dataclass_fields__
    assert not hasattr(client_facade, "send_hitl_reply")


@pytest.mark.parametrize("field", ["webhook_base_url", "webhook_signing_key"])
def test_webhook_settings_are_retired(field):
    assert field in REMOVED_FIELDS
    assert field not in Settings.model_fields
    assert field not in PRIVATE_FIELDS


def test_webhook_signing_key_is_a_retired_service_key():
    assert "webhook_signing_key" in RETIRED_SERVICE_KEYS
    assert "webhook_signing_key" not in SERVICE_KEYS


def test_ensure_service_credentials_stops_generating_and_prunes_signing_key(
    tmp_path,
):
    runtime = configured(tmp_path)
    runtime.ensure_service_credentials()
    path = runtime.home / "auth.json"
    document = json.loads(path.read_text())
    document.setdefault("services", {})["webhook_signing_key"] = "x" * 64
    path.write_text(json.dumps(document))

    services = runtime.ensure_service_credentials()

    assert "webhook_signing_key" not in services
    on_disk = json.loads(path.read_text()).get("services", {})
    assert "webhook_signing_key" not in on_disk


def test_webhook_route_group_and_install_script_are_gone():
    from api_gateway.policies import ROUTE_POLICIES

    assert "webhook" not in ROUTE_POLICIES
    assert not (BACKEND / "scripts" / "ensure_webhook_signing_key.sh").exists()
