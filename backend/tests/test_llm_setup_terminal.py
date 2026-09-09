from __future__ import annotations

import io
import termios
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import SecretStr

from llm_gateway.catalog import model_choices
from llm_gateway.setup_cli import SetupConsole, main
from llm_gateway.setup_terminal import SetupOption, select_option
from tests.fakes.llm_runtime import oauth_credential
from tests.fakes.setup_terminal import TerminalReader, mock_keyboard

OPTIONS = (SetupOption("first", "First"), SetupOption("second", "Second"))


@pytest.mark.parametrize(
    ("keys", "expected"),
    [
        (b"\r", "first"),
        (b"\x1b[B\r", "second"),
        (b"\x1b[A\r", "second"),
        (b"\x1b[B\x1b[A\r", "first"),
        (b"\x1bOB\x1bOA\n", "first"),
        (b"second\r", "first"),  # No typed-ID selection fallback.
    ],
)
def test_arrow_selection_and_default(
    monkeypatch: pytest.MonkeyPatch, keys: bytes, expected: str
) -> None:
    raw, restore = mock_keyboard(monkeypatch, keys)
    output = io.StringIO()
    assert select_option(TerminalReader(), output, "Provider", OPTIONS) == expected
    raw.assert_called_once_with(71, termios.TCSANOW)
    restore.assert_called_once_with(71, termios.TCSANOW, ["original"])
    assert "> First (default)" in output.getvalue()
    assert "Up/Down, Enter; Esc/Ctrl-C cancels" in output.getvalue()
    assert output.getvalue().endswith("\x1b[?25h")


@pytest.mark.parametrize("keys", [b"\x03", b"\x1b", b"\x1b[", b"\x04", b""])
def test_cancel_restores_terminal_and_cursor(
    monkeypatch: pytest.MonkeyPatch, keys: bytes
) -> None:
    _, restore = mock_keyboard(monkeypatch, keys)
    output = io.StringIO()
    with pytest.raises((KeyboardInterrupt, EOFError)):
        select_option(TerminalReader(), output, "Provider", OPTIONS)
    restore.assert_called_once_with(71, termios.TCSANOW, ["original"])
    assert output.getvalue().endswith("\x1b[?25h")


@pytest.mark.parametrize("stage", ["raw", "read", "write", "cursor"])
def test_terminal_restored_on_syscall_or_output_failure(
    monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    raw, restore = mock_keyboard(monkeypatch, b"\r")
    output = io.StringIO()
    if stage == "raw":
        raw.side_effect = OSError("fixture failure")
    elif stage == "read":
        monkeypatch.setattr(
            "llm_gateway.setup_terminal.os.read", Mock(side_effect=OSError)
        )
    else:
        original = output.write

        def write(value: str) -> int:
            if (
                stage == "write"
                and "First" in value
                or stage == "cursor"
                and "?25h" in value
            ):
                raise OSError("fixture failure")
            return original(value)

        monkeypatch.setattr(output, "write", write)
    with pytest.raises(OSError):
        select_option(TerminalReader(), output, "Provider", OPTIONS)
    restore.assert_called_once_with(71, termios.TCSANOW, ["original"])
    if stage != "cursor":
        assert output.getvalue().endswith("\x1b[?25h")


@pytest.mark.parametrize(
    ("keys", "provider", "auth", "text", "image"),
    [
        (b"\r\r\r\r", "openai", "api_key", "gpt-5-mini", None),
        (b"\r\r\x1b[B\r\x1b[B\r", "openai", "api_key", "gpt-4o-mini", "gpt-image-1"),
        (b"\r\x1b[B\r\r\r", "openai", "oauth", "gpt-5.5", None),
        (b"\x1b[B\r\x1b[B\r\r\r", "deepseek", "api_key", "deepseek-v4-flash", None),
        (
            b"\x1b[A\r\x1b[A\r\r\r",
            "anthropic",
            "api_key",
            "claude-haiku-4-5-20251001",
            None,
        ),
    ],
)
def test_full_setup_uses_real_key_sequences_and_eligible_choices(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    keys: bytes,
    provider: str,
    auth: str,
    text: str,
    image: str | None,
) -> None:
    _, restore = mock_keyboard(monkeypatch, keys)
    output = io.StringIO()
    reader = TerminalReader()
    verifier = AsyncMock()
    login = AsyncMock(return_value=oauth_credential(expires_at=2000000000.0))
    monkeypatch.setattr("llm_gateway.openai_oauth.login", login)
    secret = Mock(return_value=SecretStr("fixture-hidden"))
    console = SetupConsole(
        lambda title, options: select_option(reader, output, title, options),
        secret,
        lambda message: print(message, file=output),
    )
    assert (
        main(
            [],
            environment={"HOME": str(tmp_path)},
            catalog=model_choices,
            verifier=verifier,
            console=console,
        )
        == 0
    )
    selected = verifier.await_args.args[0]
    assert (selected.provider.id, selected.provider.auth) == (provider, auth)
    assert (selected.models.text, selected.models.image) == (text, image)
    has_images = provider == "openai" and auth == "api_key"
    assert restore.call_count == (4 if has_images else 3)
    assert ("None - no image generation (default)" in output.getvalue()) == has_images
    assert "(recommended) (default)" in output.getvalue()
    assert output.getvalue().index(
        "Current configuration: none saved."
    ) < output.getvalue().index("Provider (Up/Down")
    assert "fixture-hidden" not in output.getvalue()
    if provider != "openai":
        assert "OAuth" not in output.getvalue()
    if auth == "oauth":
        login.assert_awaited_once()
        secret.assert_not_called()
    else:
        login.assert_not_awaited()
        secret.assert_called_once()


@pytest.mark.parametrize("prefix", [b"", b"\r", b"\r\r", b"\r\r\r"])
@pytest.mark.parametrize("cancel", [b"\x03", b"\x1b"])
def test_menu_cancellation_never_creates_runtime_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, prefix: bytes, cancel: bytes
) -> None:
    mock_keyboard(monkeypatch, prefix + cancel)
    output, error = io.StringIO(), io.StringIO()
    secret, verifier = Mock(return_value=SecretStr("fixture-hidden")), AsyncMock()
    console = SetupConsole(
        lambda title, options: select_option(TerminalReader(), output, title, options),
        secret,
        lambda message: None,
    )
    assert (
        main(
            [],
            environment={"HOME": str(tmp_path)},
            catalog=model_choices,
            verifier=verifier,
            console=console,
            error_output=error,
        )
        == 130
    )
    assert "Setup canceled." in error.getvalue()
    assert not (tmp_path / ".hybro").exists()
    assert secret.call_count == (1 if len(prefix) >= 2 else 0)
    verifier.assert_not_awaited()


@pytest.mark.parametrize("state", ["none", "oauth", "api_key", "invalid"])
def test_current_config_summary_is_read_only_and_never_reads_credentials(
    tmp_path, monkeypatch, state
):
    import json

    from llm_gateway.runtime_store import RuntimeConfigStore
    from llm_gateway.setup_cli import _show_current_config
    from llm_gateway.setup_service import SetupService
    from tests.fakes.llm_runtime import oauth_config, runtime_state

    store = RuntimeConfigStore(tmp_path / "runtime")
    if state != "none":
        store.home.mkdir(mode=0o700)
        config = oauth_config() if state == "oauth" else runtime_state().config
        data = config.model_dump()
        if state == "api_key":
            data["models"]["image"] = "gpt-image-1"
        if state == "invalid":
            data["models"]["text"] = "private-marker\u001b[31m"
        (store.home / "config.yaml").write_text(json.dumps(data))
    before = (
        {p.name: p.read_bytes() for p in store.home.iterdir()}
        if store.home.exists()
        else {}
    )
    original_read = store._read

    def read_config_only(name):
        assert name == "config.yaml", "Status must not read credentials"
        return original_read(name)

    monkeypatch.setattr(store, "_read", read_config_only)
    verify, write = AsyncMock(), Mock()
    console = SetupConsole(Mock(), Mock(), write)
    _show_current_config(SetupService(store, model_choices, verify), console)
    output = "\n".join(call.args[0] for call in write.call_args_list)
    assert "private-marker" not in output
    if state == "none":
        assert "none saved" in output
        assert not store.home.exists()
    elif state == "invalid":
        assert "invalid or unreadable" in output
    else:
        assert "Provider: OpenAI" in output
        assert ("ChatGPT/Codex OAuth" if state == "oauth" else "API key") in output
        assert f"Text model: {config.models.text}" in output
        assert (
            "Image model: None" if state == "oauth" else "Image model: gpt-image-1"
        ) in output
        assert "backend activation are not checked" in output
    after = (
        {p.name: p.read_bytes() for p in store.home.iterdir()}
        if store.home.exists()
        else {}
    )
    assert after == before
    verify.assert_not_awaited()
    console.select.assert_not_called()
    console.read_secret.assert_not_called()


def test_terminal_control_error_is_actionable_without_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mock_keyboard(monkeypatch, b"\r")
    monkeypatch.setattr(
        "llm_gateway.setup_terminal.termios.tcgetattr",
        Mock(side_effect=termios.error("fixture-sensitive-terminal-error")),
    )
    output, error = io.StringIO(), io.StringIO()
    secret, verifier = Mock(), AsyncMock()
    console = SetupConsole(
        lambda title, options: select_option(TerminalReader(), output, title, options),
        secret,
        lambda message: None,
    )
    assert (
        main(
            [],
            environment={"HOME": str(tmp_path)},
            catalog=model_choices,
            verifier=verifier,
            console=console,
            error_output=error,
        )
        == 1
    )
    assert "check the private runtime directory and terminal" in error.getvalue()
    assert "fixture-sensitive" not in error.getvalue()
    assert not (tmp_path / ".hybro").exists()
    secret.assert_not_called()
    verifier.assert_not_awaited()
