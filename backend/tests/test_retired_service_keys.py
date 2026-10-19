"""Retired auth.json service keys are accepted on read and pruned on rewrite."""

from __future__ import annotations

import json

import pytest

import configuration_cli as cli
from common.config import loader
from common.config.loader import PRIVATE_FIELDS, Settings
from common.config.runtime_config import RuntimeConfigurationError
from common.config.runtime_store import (
    RETIRED_SERVICE_KEYS,
    SERVICE_KEYS,
    _auth_object,
)
from tests.test_json_runtime_config import configured


def _add_services(runtime, **services: str) -> None:
    path = runtime.home / "auth.json"
    document = json.loads(path.read_text())
    document.setdefault("services", {}).update(services)
    path.write_text(json.dumps(document))


def _services_on_disk(runtime) -> dict[str, str]:
    return json.loads((runtime.home / "auth.json").read_text()).get("services", {})


def test_mongodb_password_is_retired_not_a_service_key():
    assert RETIRED_SERVICE_KEYS == {"mongodb_password"}
    assert not RETIRED_SERVICE_KEYS & SERVICE_KEYS


def test_mongodb_password_is_no_longer_a_setting():
    assert "mongodb_password" not in Settings.model_fields
    assert "mongodb_password" not in PRIVATE_FIELDS


def test_auth_json_with_retired_key_still_reads(tmp_path):
    runtime = configured(tmp_path)
    _add_services(runtime, mongodb_password="fixture-retired")

    runtime.load({})

    assert "mongodb_password" not in runtime.read_service_credentials()


def test_unknown_service_key_is_still_rejected():
    payload = json.dumps({"services": {"not_a_service": "x"}}).encode()

    with pytest.raises(RuntimeConfigurationError):
        _auth_object(payload)


def test_ensure_service_credentials_prunes_retired_key(tmp_path):
    runtime = configured(tmp_path)
    runtime.ensure_service_credentials()
    _add_services(runtime, mongodb_password="fixture-retired")

    services = runtime.ensure_service_credentials()

    assert "mongodb_password" not in services
    assert "mongodb_password" not in _services_on_disk(runtime)
    assert services == _services_on_disk(runtime)


def test_ensure_service_credentials_keeps_live_secrets_when_pruning(tmp_path):
    runtime = configured(tmp_path)
    before = runtime.ensure_service_credentials()
    _add_services(runtime, mongodb_password="fixture-retired")

    after = runtime.ensure_service_credentials()

    assert after == before


def test_get_settings_ignores_retired_service_key(tmp_path, monkeypatch):
    runtime = configured(tmp_path)
    _add_services(runtime, mongodb_password="fixture-retired")
    monkeypatch.setenv("HYBRO_HOME", str(runtime.home))
    loader.get_settings.cache_clear()
    try:
        settings = loader.get_settings()
    finally:
        loader.get_settings.cache_clear()

    assert not hasattr(settings, "mongodb_password")


def test_config_secret_rejects_retired_key(tmp_path, monkeypatch):
    runtime = configured(tmp_path)
    monkeypatch.setenv("HYBRO_HOME", str(runtime.home))

    assert cli.main(["config", "secret", "mongodb_password"]) == 1

    assert "mongodb_password" not in _services_on_disk(runtime)


def test_env_migration_does_not_import_retired_key(tmp_path, monkeypatch):
    runtime = configured(tmp_path)
    (runtime.home / "config.json").unlink()
    (runtime.home / "config.yaml").write_text(
        "version: 1\nprovider: {id: openai, auth: api_key}\n"
        "models: {text: gpt-4o-mini}\n"
    )
    env = tmp_path / "legacy.env"
    env.write_text("MONGODB_PASSWORD=fixture-retired\nLOG_LEVEL=DEBUG\n")
    monkeypatch.setenv("HYBRO_HOME", str(runtime.home))

    assert cli.main(["config", "migrate", "--from-env", str(env)]) == 0

    assert "mongodb_password" not in _services_on_disk(runtime)
    assert "mongodb_password" not in runtime.read_config().backend


def test_setup_save_reads_auth_json_carrying_retired_key(tmp_path):
    from pydantic import SecretStr

    from common.config.runtime_config import ApiKeyCredential, RuntimeConfig

    runtime = configured(tmp_path)
    live = runtime.ensure_service_credentials()
    _add_services(runtime, mongodb_password="fixture-retired")

    runtime.save(
        RuntimeConfig.model_validate(
            {
                "provider": {"id": "openai", "auth": "api_key"},
                "models": {"text": "gpt-other"},
            }
        ),
        ApiKeyCredential(provider="openai", api_key=SecretStr("fixture-rotated")),
        {},
    )

    assert runtime.read_service_credentials() == live
    credential = runtime.load({}).authentication.credential
    assert credential.api_key.get_secret_value() == "fixture-rotated"
