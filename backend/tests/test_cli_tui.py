"""TUI keyboard/syscall fakes only; never launch lifecycle commands or a PTY."""

import io
from unittest.mock import Mock

import pytest

from llm_gateway import cli_tui
from llm_gateway.setup_cli import SetupConsole
from llm_gateway.setup_terminal import SetupOption, select_option
from tests.fakes.setup_terminal import TerminalReader, mock_keyboard


def console_with(*choices):
    pending = iter(choices)

    def select(title, options):
        try:
            chosen = next(pending)
        except StopIteration:
            raise EOFError from None
        if isinstance(chosen, type) and issubclass(chosen, BaseException):
            raise chosen
        assert chosen in {o.value for o in options}, title
        return chosen

    return SetupConsole(
        select, Mock(side_effect=AssertionError("secret")), Mock(), Mock()
    )


@pytest.mark.parametrize("action", ["status", "logs", "help"])
def test_read_or_setup_action_dispatches_existing_command(action):
    run = Mock(return_value=0)
    choices = ("services", action)
    console = console_with(*choices)
    assert cli_tui.main(console=console, run=run) == 130
    run.assert_called_once_with((action,))
    console.pause.assert_called_once_with()


@pytest.mark.parametrize("action", ["start", "restart", "stop"])
def test_ordinary_lifecycle_actions_do_not_repeat_confirmation(action):
    run = Mock(return_value=0)
    choices = ("services", action)
    console = console_with(*choices)
    assert cli_tui.main(console=console, run=run) == 130
    run.assert_called_once_with((action,))
    console.pause.assert_called_once_with()


@pytest.mark.parametrize("action", ["down", "recreate", "build_recreate"])
@pytest.mark.parametrize("confirmation", ["run", "cancel"])
def test_container_removal_and_recreation_require_confirmation(action, confirmation):
    run = Mock(return_value=0)
    console = console_with("services", action, confirmation)
    assert cli_tui.main(console=console, run=run) == 130
    if confirmation == "run":
        expected = (
            ("down",) if action == "down" else ("start", *cli_tui._START_MODES[action])
        )
        run.assert_called_once_with(expected)
        console.pause.assert_called_once_with()
    else:
        run.assert_not_called()
        console.pause.assert_not_called()


@pytest.mark.parametrize("mode,flags", list(cli_tui._START_MODES.items()))
def test_start_modes_preserve_explicit_flags(mode, flags):
    run = Mock(return_value=0)
    choices = ("services", mode) + (("run",) if mode != "build" else ())
    assert cli_tui.main(console=console_with(*choices), run=run) == 130
    run.assert_called_once_with(("start", *flags))


def test_failed_command_returns_to_menu_without_automatic_retry():
    run = Mock(side_effect=[1, 0])
    console = console_with("services", "status", "help")
    assert cli_tui.main(console=console, run=run) == 130
    assert [c.args[0] for c in run.call_args_list] == [("status",), ("help",)]
    console.write.assert_any_call("Command failed.")
    assert console.pause.call_count == 2


def test_pages_switch_without_commands():
    run = Mock()
    assert cli_tui.main(console=console_with("services", "models"), run=run) == 130
    run.assert_not_called()


@pytest.mark.parametrize("keys", [b"\x1b", b"\x03", b"\x04", b""])
def test_real_key_cancellation_has_no_effect(monkeypatch, keys):
    _, restore = mock_keyboard(monkeypatch, keys)
    output, run = io.StringIO(), Mock()
    console = SetupConsole(
        lambda title, options: select_option(TerminalReader(), output, title, options),
        Mock(),
        Mock(),
    )
    expected = 0 if keys == b"\x1b" else 130
    assert cli_tui.main(console=console, run=run, output=output) == expected
    run.assert_not_called()
    restore.assert_called_once()


@pytest.mark.parametrize(
    "keys,error",
    [
        (b" \x1b[B\r", None),
        (b"\x1b", KeyboardInterrupt),
        (b"\x03", KeyboardInterrupt),
        (b"", EOFError),
    ],
)
def test_enter_pause_restores_terminal_without_a_menu(monkeypatch, keys, error):
    from llm_gateway.setup_terminal import wait_for_enter

    _, restore = mock_keyboard(monkeypatch, keys)
    output = io.StringIO()
    if error is None:
        wait_for_enter(TerminalReader(), output)
    else:
        with pytest.raises(error):
            wait_for_enter(TerminalReader(), output)
    restore.assert_called_once()
    assert "Press Enter to return" in output.getvalue()
    assert "(default)" not in output.getvalue()


def test_down_default_confirmation_cancels_with_real_arrows(monkeypatch):
    # Tab -> Services -> down -> default Cancel -> Escape -> model page EOF.
    _, restore = mock_keyboard(monkeypatch, b"\t" + b"\x1b[B" * 8 + b"\r\r\x1b")
    output, run = io.StringIO(), Mock()
    console = SetupConsole(
        lambda title, options: select_option(TerminalReader(), output, title, options),
        Mock(),
        Mock(),
    )
    assert cli_tui.main(console=console, run=run, output=output) == 130
    run.assert_not_called()
    assert restore.call_count == 5


def test_small_terminal_scrolls_model_list_without_wrapping(monkeypatch):
    from llm_gateway import setup_terminal

    mock_keyboard(monkeypatch, b"\x1b[A\r")
    monkeypatch.setattr(setup_terminal.os, "get_terminal_size", lambda fd: (32, 7))
    output = io.StringIO()
    options = tuple(
        SetupOption(str(i), "Model " + str(i) + " long label" * 10) for i in range(10)
    )
    assert select_option(TerminalReader(), output, "Models", options) == "9"
    assert "Showing 7-10 of 10" in output.getvalue()
    assert "\x1b[5A" in output.getvalue()
    assert "\x1b[10A" not in output.getvalue()
    import re

    plain = re.sub(r"\x1b\[[?0-9;]*[A-Za-z]", "", output.getvalue())
    assert max(map(len, plain.splitlines())) <= 31


def test_runner_uses_argv_without_shell_or_credential_arguments(monkeypatch):
    run = Mock(return_value=Mock(returncode=7))
    monkeypatch.setattr(cli_tui.subprocess, "run", run)
    assert cli_tui._run_command(("status",)) == 7
    args, kwargs = run.call_args
    assert args[0][-1] == "status"
    assert args[0][0].endswith("/scripts/hybro")
    assert kwargs == {"check": False}


def test_explicit_provider_clients_and_keys_do_not_import_app_settings(monkeypatch):
    import builtins

    from llm_gateway.providers import deepseek_provider, openai_provider

    original = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "common.config.settings":
            pytest.fail("Explicit CLI credentials must not load application settings")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    openai_factory, deepseek_factory = Mock(), Mock()
    monkeypatch.setattr(openai_provider, "AsyncOpenAI", openai_factory)
    monkeypatch.setattr(deepseek_provider, "AsyncOpenAI", deepseek_factory)
    injected = Mock()
    assert openai_provider.OpenAIProvider(client=injected)._client is injected
    assert deepseek_provider.DeepSeekProvider(client=injected)._client is injected
    openai_provider.OpenAIProvider(
        api_key="fixture", base_url="https://fixture.invalid/v1"
    )
    deepseek_provider.DeepSeekProvider(api_key="fixture")
    openai_factory.assert_called_once_with(
        api_key="fixture", base_url="https://fixture.invalid/v1", max_retries=0
    )
    deepseek_factory.assert_called_once_with(
        api_key="fixture", base_url="https://api.deepseek.com", max_retries=0
    )


@pytest.mark.parametrize("configured", [False, True])
def test_legacy_provider_defaults_remain_settings_backed(monkeypatch, configured):
    import importlib
    from types import SimpleNamespace

    from llm_gateway.providers import deepseek_provider, openai_provider

    settings_module = importlib.import_module("common.config.settings")
    monkeypatch.setattr(
        settings_module,
        "settings",
        SimpleNamespace(
            openai_api_key="fixture-openai" if configured else "",
            openai_base_url="https://fixture.invalid/v1" if configured else None,
            deepseek_api_key="fixture-deepseek" if configured else "",
        ),
    )
    openai_factory, deepseek_factory = Mock(), Mock()
    monkeypatch.setattr(openai_provider, "AsyncOpenAI", openai_factory)
    monkeypatch.setattr(deepseek_provider, "AsyncOpenAI", deepseek_factory)
    openai_provider.OpenAIProvider()
    deepseek_provider.DeepSeekProvider()
    openai_factory.assert_called_once_with(
        api_key="fixture-openai" if configured else "missing",
        base_url="https://fixture.invalid/v1" if configured else None,
        max_retries=0,
    )
    deepseek_factory.assert_called_once_with(
        api_key="fixture-deepseek" if configured else "missing",
        base_url="https://api.deepseek.com",
        max_retries=0,
    )


def test_package_initializer_defers_application_imports(monkeypatch):
    import builtins
    import importlib.util
    from pathlib import Path

    path = Path(cli_tui.__file__).with_name("__init__.py")
    spec = importlib.util.spec_from_file_location("gateway_import_probe", path)
    module = importlib.util.module_from_spec(spec)
    original = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name in {"gateway", "model_registry", "common.config.settings"}:
            pytest.fail("Package initializer must not load application defaults")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    spec.loader.exec_module(module)
    assert module.__all__ == ["LLMGatewayImpl", "ModelRegistryImpl"]
    assert "LLMGatewayImpl" not in vars(module)
    assert "ModelRegistryImpl" not in vars(module)


def test_public_gateway_exports_still_resolve_original_classes():
    from llm_gateway import LLMGatewayImpl, ModelRegistryImpl
    from llm_gateway.gateway import LLMGatewayImpl as OriginalGateway
    from llm_gateway.model_registry import ModelRegistryImpl as OriginalRegistry

    assert LLMGatewayImpl is OriginalGateway
    assert ModelRegistryImpl is OriginalRegistry
