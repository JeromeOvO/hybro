from __future__ import annotations

import re
import stat
import subprocess
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "ensure_webhook_signing_key.sh"
)


def _run_helper(env_file: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(SCRIPT), str(env_file)],
        check=True,
        capture_output=True,
        text=True,
    )


def _read_signing_key(env_file: Path) -> str:
    for line in env_file.read_text().splitlines():
        if line.startswith("WEBHOOK_SIGNING_KEY="):
            return line.partition("=")[2]
    raise AssertionError("WEBHOOK_SIGNING_KEY was not written")


def test_generates_and_persists_key_when_value_is_empty(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("APP_ENV=development\nWEBHOOK_SIGNING_KEY=\n")

    first_result = _run_helper(env_file)
    generated_key = _read_signing_key(env_file)
    second_result = _run_helper(env_file)

    assert re.fullmatch(r"[0-9a-f]{64}", generated_key)
    assert "Generated a persistent WEBHOOK_SIGNING_KEY" in first_result.stdout
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert second_result.stdout == ""
    assert _read_signing_key(env_file) == generated_key


def test_preserves_existing_key_and_file_contents(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    original = "APP_ENV=development\nWEBHOOK_SIGNING_KEY=" + ("x" * 32) + "\n"
    env_file.write_text(original)

    result = _run_helper(env_file)

    assert result.stdout == ""
    assert env_file.read_text() == original


def test_appends_key_when_setting_is_missing(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("APP_ENV=development\n")

    _run_helper(env_file)

    assert env_file.read_text().startswith("APP_ENV=development\n")
    assert re.fullmatch(r"[0-9a-f]{64}", _read_signing_key(env_file))
