"""There is one user directory and no repository dotenv lookup."""

import pytest

from llm_gateway.runtime_config import RuntimeConfigurationError
from llm_gateway.runtime_store import runtime_home


def test_runtime_home_defaults_to_user_directory(tmp_path):
    assert runtime_home({"HOME": str(tmp_path)}) == tmp_path / ".hybro"


def test_runtime_home_uses_explicit_absolute_override(tmp_path):
    target = tmp_path / "custom"
    assert runtime_home({"HYBRO_HOME": str(target), "HOME": "/unused"}) == target


def test_runtime_home_rejects_relative_override():
    with pytest.raises(RuntimeConfigurationError, match="absolute"):
        runtime_home({"HYBRO_HOME": "relative"})
