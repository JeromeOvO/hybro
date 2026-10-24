"""Settings-page regressions with synthetic storage, keyboard and Provider calls."""

import io
import re
import signal
from unittest.mock import AsyncMock, Mock

import pytest

from common.config.runtime_store import RuntimeConfigStore
from llm_gateway.catalog import model_choices
from llm_gateway.cli_tui import main
from llm_gateway.setup_cli import SetupConsole, _terminal_console
from llm_gateway.setup_panel import SetupPanel
from llm_gateway.setup_service import SetupError, SetupService
from llm_gateway.setup_terminal import SelectionCancelled, SetupOption, _draw_screen
from llm_gateway.tui_brand import SELECTED_STYLE
from tests.fakes.llm_runtime import oauth_config, oauth_credential
from tests.fakes.setup_terminal import TerminalReader, mock_keyboard
from tests.test_cli_tui import console_with


def configured(tmp_path, console, verifier=None):
    store = RuntimeConfigStore(tmp_path / "runtime")
    store.save(oauth_config(), oauth_credential(), {})
    service = SetupService(store, model_choices, verifier or AsyncMock())
    return SetupPanel(service, console, {}), store


def contents(store):
    return {
        name: (store.home / name).read_bytes() for name in ("config.json", "auth.json")
    }


def test_model_only_edit_reuses_auth_and_does_not_save_until_requested(
    tmp_path, monkeypatch
):
    console = console_with("gpt-6.1-sol")
    verifier = AsyncMock()
    panel, store = configured(tmp_path, console, verifier)
    before = contents(store)
    login = AsyncMock(side_effect=AssertionError("Must reuse stored OAuth"))
    monkeypatch.setattr("llm_gateway.openai_oauth.login", login)
    original = console.select
    seen = []

    def select(title, options):
        seen.append((title, options))
        return original(title, options)

    panel.console = SetupConsole(select, console.read_secret, console.write)
    panel.edit("model")
    assert [title for title, _ in seen] == ["Text model"]
    assert next(option.value for option in seen[0][1] if option.current) == "gpt-6-sol"
    assert panel.draft.models.text == "gpt-6.1-sol" and panel.dirty
    assert contents(store) == before
    verifier.assert_not_awaited()
    previous_signal = signal.getsignal(signal.SIGINT)
    panel.edit("save")
    assert signal.getsignal(signal.SIGINT) == previous_signal
    verifier.assert_awaited_once()
    assert store.load({}).config.models.text == "gpt-6.1-sol"
    assert not panel.dirty and "Start services" in panel.notice
    assert next(option.value for option in panel.options() if option.current) == "model"
    login.assert_not_awaited()
    console.read_secret.assert_not_called()


def test_model_picker_escape_preserves_focus_and_saved_values(tmp_path):
    console = console_with(SelectionCancelled)
    panel, store = configured(tmp_path, console)
    before = contents(store)
    with pytest.raises(SelectionCancelled):
        panel.edit("model")
    assert panel.draft == panel.saved and not panel.dirty
    assert next(option.value for option in panel.options() if option.current) == "model"
    assert contents(store) == before


def test_failed_verification_keeps_draft_and_files_and_restores_signal(tmp_path):
    console = console_with("gpt-6.1-sol")
    verifier = AsyncMock(side_effect=RuntimeError("private-provider-content"))
    panel, store = configured(tmp_path, console, verifier)
    before = contents(store)
    panel.edit("model")
    previous_signal = signal.getsignal(signal.SIGINT)
    with pytest.raises(SetupError) as exc:
        panel.edit("save")
    assert "private-provider-content" not in str(exc.value)
    assert signal.getsignal(signal.SIGINT) == previous_signal
    assert panel.dirty and panel.draft.models.text == "gpt-6.1-sol"
    assert contents(store) == before


def test_first_connection_authenticates_before_models_and_exit_does_not_save(
    tmp_path, monkeypatch
):
    store = RuntimeConfigStore(tmp_path / "runtime")
    verifier, login = AsyncMock(), AsyncMock(return_value=oauth_credential())
    monkeypatch.setattr("llm_gateway.openai_oauth.login", login)
    choices = iter(["openai", "oauth", "gpt-6.1-sol", "keep", "discard"])

    def select(title, options):
        if title == "Text model":
            login.assert_awaited_once()
        return next(choices)

    panel = SetupPanel(
        SetupService(store, model_choices, verifier),
        SetupConsole(select, Mock(), Mock()),
        {},
    )
    assert not store.home.exists()
    panel.edit("connection")
    assert panel.dirty and not store.home.exists()
    assert not panel.can_exit()
    assert panel.can_exit()
    verifier.assert_not_awaited()
    assert not store.home.exists()


def test_cancel_is_not_a_failed_command_and_services_keep_model_draft(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("llm_gateway.cli_tui._read_status", lambda: [])
    console = console_with(
        "model",
        "gpt-6.1-sol",
        "services",
        "stop",
        "models",
        SelectionCancelled,
        "discard",
    )
    panel, store = configured(tmp_path, console)
    before = contents(store)
    run = Mock(return_value=130)
    assert main(console=console, service=panel.service, environment={}, run=run) == 0
    run.assert_called_once_with(("stop",))
    assert not any(
        call.args == ("Command failed.",) for call in console.write.call_args_list
    )
    assert contents(store) == before


def test_expired_draft_authentication_is_reacquired_before_save(tmp_path, monkeypatch):
    console = console_with("gpt-6.1-sol")
    panel, store = configured(tmp_path, console)
    panel.edit("model")
    expires = panel.prepared.credential.expires_at
    updated = oauth_credential(expires_at=expires + 3600)
    login = AsyncMock(return_value=updated)
    monkeypatch.setattr("llm_gateway.openai_oauth.login", login)
    monkeypatch.setattr("llm_gateway.setup_panel.time.time", lambda: expires + 1)
    panel.edit("save")
    login.assert_awaited_once()
    assert store.load({}).authentication.credential == updated


def test_view_does_not_read_auth_and_terminal_errors_do_not_retry(
    tmp_path, monkeypatch
):
    console = SetupConsole(Mock(side_effect=OSError("fixture")), Mock(), Mock())
    panel, store = configured(tmp_path, console)
    original = store._read

    def config_only(name):
        assert name == "config.json"
        return original(name)

    monkeypatch.setattr(store, "_read", config_only)
    assert main(console=console, service=panel.service, environment={}) == 1
    console.select.assert_called_once()
    console.read_secret.assert_not_called()


@pytest.mark.parametrize("size", [(80, 24), (120, 32), (32, 10), (16, 5)])
def test_panel_layout_bounds_hierarchy_and_save_hint(tmp_path, size):
    panel, _ = configured(tmp_path, console_with())
    columns, rows = size
    for selected in (1, 2, 3):  # Model, save, and the tab destination.
        output = io.StringIO()
        _draw_screen(output, panel.title(), panel.options(), selected, columns, rows)
        spans = re.findall(
            r"\x1b\[(\d+);(\d+)H\x1b\[([\d;]+)m([^\x1b]*)\x1b\[0m", output.getvalue()
        )
        assert spans
        for row, column, _, text in spans:
            assert 1 <= int(row) < rows
            assert 1 <= int(column) and int(column) + len(text) - 1 <= columns
        painted = "\n".join(text for _, _, _, text in spans)
        assert "(current)" not in painted and "Services [Tab]" not in painted
        if columns >= 32:
            assert "gpt-6-sol" in painted
            assert "Models     Services" in painted
            assert any(
                style == "1;4" and text == "Models" for _, _, style, text in spans
            )
            if selected == 2:
                assert "Quota/billing applies" in painted
            if selected == 3:
                assert any(
                    style == SELECTED_STYLE and text == "Services"
                    for _, _, style, text in spans
                )
        if columns >= 80:
            assert int(spans[0][0]) > 1 and int(spans[0][1]) > 1
            assert "Saved locally" in painted


def test_fixed_screen_redraw_current_value_and_restoration(monkeypatch):
    reader, writer = TerminalReader(), io.StringIO()
    mock_keyboard(monkeypatch, b"\r\t\x1b")
    # Inspect output before closing the in-memory writer.
    writer.close = lambda: None
    monkeypatch.setattr(
        "builtins.open", lambda path, mode="r", **kw: reader if mode == "r" else writer
    )
    with _terminal_console(io.StringIO(), screen=True) as console:
        assert (
            console.select(
                "Models", (SetupOption("a", "A"), SetupOption("b", "B", current=True))
            )
            == "b"
        )
        assert (
            console.select(
                "Models",
                (
                    SetupOption("a", "A"),
                    SetupOption("services", "Services", shortcut=b"\t"),
                ),
            )
            == "services"
        )
        with pytest.raises(SelectionCancelled):
            console.select("Picker", (SetupOption("a", "A"),))
    text = writer.getvalue()
    assert text.startswith("\x1b[?1049h")
    assert text.count("\x1b[2J\x1b[H") == 3
    assert text.endswith("\x1b[?25h\x1b[?1049l")
    assert "> B [current]" in text
    assert f"\x1b[{SELECTED_STYLE}m> B [current]" in text


def test_start_after_save_recreates_so_the_new_connection_applies(
    tmp_path, monkeypatch
):
    """Compose cannot see config.json changes, so a plain start kept old settings."""
    monkeypatch.setattr(
        "llm_gateway.openai_oauth.login",
        AsyncMock(side_effect=AssertionError("Must reuse stored OAuth")),
    )
    store = RuntimeConfigStore(tmp_path / "runtime")
    store.save(oauth_config(), oauth_credential(), {})
    service = SetupService(store, model_choices, AsyncMock())
    run = Mock(return_value=0)
    console = console_with(
        "model", "gpt-6.1-sol", "save", "services", "start", "start", "models"
    )
    assert main(console=console, run=run, service=service, environment={}) == 130
    assert store.load({}).config.models.text == "gpt-6.1-sol"
    # Applied once; the next start in the same session is an ordinary start.
    assert [c.args[0] for c in run.call_args_list] == [
        ("start", "--recreate"),
        ("start",),
    ]
    console.write.assert_any_call("Running hybro start --recreate ...")


def test_failed_apply_keeps_the_saved_configuration_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "llm_gateway.openai_oauth.login",
        AsyncMock(side_effect=AssertionError("Must reuse stored OAuth")),
    )
    store = RuntimeConfigStore(tmp_path / "runtime")
    store.save(oauth_config(), oauth_credential(), {})
    service = SetupService(store, model_choices, AsyncMock())
    run = Mock(side_effect=[1, 0])
    console = console_with("save", "services", "start", "start")
    assert main(console=console, run=run, service=service, environment={}) == 130
    assert [c.args[0] for c in run.call_args_list] == [
        ("start", "--recreate"),
        ("start", "--recreate"),
    ]


def test_retired_saved_model_asks_for_a_current_model(tmp_path):
    store = RuntimeConfigStore(tmp_path / "runtime")
    store.save(oauth_config(), oauth_credential(), {})
    path = store.home / "config.json"
    path.write_text(path.read_text().replace('"gpt-6-sol"', '"gpt-5.5"'))
    service = SetupService(store, model_choices, AsyncMock())
    panel = SetupPanel(service, console_with(), {})
    assert panel.saved is None and "no longer offered" in panel.notice


def test_first_run_notice_says_what_to_do(tmp_path):
    store = RuntimeConfigStore(tmp_path / "runtime")
    panel = SetupPanel(
        SetupService(store, model_choices, AsyncMock()), console_with(), {}
    )
    assert panel.saved is None and "Choose Connection" in panel.notice
