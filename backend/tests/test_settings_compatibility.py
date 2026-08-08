import pytest
from pydantic import ValidationError

from common.config.settings import Settings


def test_eventing_channel_accepts_legacy_environment_alias(monkeypatch):
    monkeypatch.delenv("EVENTING_REDIS_CHANNEL", raising=False)
    monkeypatch.setenv("REDIS_INTERNAL_CHANNEL", "legacy:internal")

    configured = Settings(_env_file=None)

    assert configured.eventing_redis_channel == "legacy:internal"


def test_new_eventing_channel_environment_name_wins_over_legacy(monkeypatch):
    monkeypatch.setenv("REDIS_INTERNAL_CHANNEL", "legacy:internal")
    monkeypatch.setenv("EVENTING_REDIS_CHANNEL", "current:internal")

    configured = Settings(_env_file=None)

    assert configured.eventing_redis_channel == "current:internal"


def test_eventing_auxiliary_task_capacity_defaults_and_requires_positive(monkeypatch):
    monkeypatch.delenv("EVENTING_AUXILIARY_TASK_MAXSIZE", raising=False)
    assert Settings(_env_file=None).eventing_auxiliary_task_maxsize == 128

    monkeypatch.setenv("EVENTING_AUXILIARY_TASK_MAXSIZE", "0")
    with pytest.raises(ValidationError, match="eventing_auxiliary_task_maxsize"):
        Settings(_env_file=None)
