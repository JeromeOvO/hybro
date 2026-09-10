from __future__ import annotations

import re
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ensure_registrar_token.sh"


def _run_helper(env_file: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/sh", "-x", str(SCRIPT), str(env_file)],
        env={"PATH": "/usr/bin:/bin", "HOME": str(env_file.parent)},
        check=True,
        capture_output=True,
        text=True,
    )


def _read_var(env_file: Path, key: str) -> str:
    for line in env_file.read_text().splitlines():
        if line.startswith(f"{key}="):
            return line.partition("=")[2]
    raise AssertionError(f"{key} was not written")


def test_generates_matching_tokens_when_empty(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "APP_ENV=development\nDEFAULT_AGENT_REGISTRAR_TOKEN=\nAGENT_REGISTRAR_TOKEN=\n"
    )

    first_result = _run_helper(env_file)
    backend_token = _read_var(env_file, "DEFAULT_AGENT_REGISTRAR_TOKEN")
    agents_token = _read_var(env_file, "AGENT_REGISTRAR_TOKEN")
    inference_token = _read_var(env_file, "DEFAULT_AGENT_LLM_TOKEN")
    second_result = _run_helper(env_file)

    assert re.fullmatch(r"[0-9a-f]{64}", backend_token)
    assert backend_token == agents_token
    assert re.fullmatch(r"[0-9a-f]{64}", inference_token)
    assert inference_token != backend_token
    assert inference_token not in first_result.stdout + first_result.stderr
    assert backend_token not in first_result.stdout + first_result.stderr
    assert _read_var(env_file, "DEFAULT_AGENT_LLM_TOKEN") == inference_token
    assert "Configured a shared default-agent registrar token" in first_result.stdout
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert second_result.stdout == ""
    assert _read_var(env_file, "DEFAULT_AGENT_REGISTRAR_TOKEN") == backend_token


def test_adopts_existing_backend_token(tmp_path: Path) -> None:
    token = "a" * 64
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"DEFAULT_AGENT_REGISTRAR_TOKEN={token}\nAGENT_REGISTRAR_TOKEN=\n"
    )

    result = _run_helper(env_file)

    assert _read_var(env_file, "DEFAULT_AGENT_REGISTRAR_TOKEN") == token
    assert _read_var(env_file, "AGENT_REGISTRAR_TOKEN") == token
    assert "Configured a shared default-agent registrar token" in result.stdout


@pytest.mark.parametrize("inference_token", ["", "c" * 64])
def test_preserves_matching_tokens(tmp_path: Path, inference_token: str) -> None:
    token = "b" * 64
    env_file = tmp_path / ".env"
    original = (
        f"APP_ENV=development\nDEFAULT_AGENT_REGISTRAR_TOKEN={token}\n"
        f"AGENT_REGISTRAR_TOKEN={token}\nDEFAULT_AGENT_LLM_TOKEN={inference_token}\n"
    )
    env_file.write_text(original)

    result = _run_helper(env_file)

    assert result.stdout == ""
    assert _read_var(env_file, "DEFAULT_AGENT_REGISTRAR_TOKEN") == token
    assert _read_var(env_file, "AGENT_REGISTRAR_TOKEN") == token
    if inference_token:
        assert env_file.read_text() == original
    else:
        assert re.fullmatch(
            r"[0-9a-f]{64}", _read_var(env_file, "DEFAULT_AGENT_LLM_TOKEN")
        )
