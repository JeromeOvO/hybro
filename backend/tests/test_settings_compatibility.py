import pytest
from pydantic import ValidationError

from common.config.loader import Settings


def test_old_environment_aliases_are_not_configuration_sources(monkeypatch):
    monkeypatch.setenv("REDIS_INTERNAL_CHANNEL", "legacy:internal")
    monkeypatch.setenv("EVENTING_REDIS_CHANNEL", "ambient:internal")
    assert Settings().eventing_redis_channel == "internal:global"


def test_explicit_configuration_selects_eventing_channel():
    assert (
        Settings(eventing_redis_channel="configured:internal").eventing_redis_channel
        == "configured:internal"
    )


def test_eventing_auxiliary_capacity_is_positive():
    assert Settings().eventing_auxiliary_task_maxsize == 128
    with pytest.raises(ValidationError, match="eventing_auxiliary_task_maxsize"):
        Settings(eventing_auxiliary_task_maxsize=0)
