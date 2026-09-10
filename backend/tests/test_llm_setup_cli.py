from __future__ import annotations

import asyncio
import getpass
import io
import os
import shutil
import signal
import subprocess
import sys
import time
import warnings
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import SecretStr

from common.config.runtime_config import (
    ApiKeyCredential,
    ResolvedCredential,
    RuntimeConfig,
    RuntimeProvider,
)
from common.config.runtime_store import RuntimeConfigStore
from llm_gateway.setup_cli import SetupConsole, _terminal_console, main
from llm_gateway.setup_service import ModelChoices, SetupError
from llm_gateway.setup_terminal import SetupOption
from tests.fakes.llm_runtime import oauth_credential
from tests.fakes.setup_terminal import TerminalReader, mock_keyboard

# Deliberately not a production catalog or a claim of Provider model support.
CHOICES = ModelChoices(("fixture-text", "fixture-text-2"), ("fixture-image",))


def catalog(provider: RuntimeProvider) -> ModelChoices:
    return CHOICES if provider.id == "openai" else ModelChoices(CHOICES.text)


def arguments(provider: str = "openai") -> list[str]:
    return [
        "--non-interactive",
        "--provider",
        provider,
        "--auth",
        "api_key",
        "--text-model",
        "fixture-text",
        "--image-model",
        "none",
    ]


def config(provider: str = "openai", auth: str = "api_key") -> RuntimeConfig:
    return RuntimeConfig.model_validate(
        {"provider": {"id": provider, "auth": auth}, "models": {"text": "fixture-text"}}
    )


def key(provider: str = "openai", value: str = "fixture-stored") -> ApiKeyCredential:
    return ApiKeyCredential.model_validate(
        {"provider": provider, "api_key": SecretStr(value)}
    )


def console(
    answers: list[str], output: list[str], secret: str = "fixture-hidden"
) -> SetupConsole:
    iterator = iter(answers)

    def select(title: str, options: tuple[SetupOption, ...]) -> str:
        output.append(title + ": " + ", ".join(option.label for option in options))
        answer = next(iterator) or options[0].value
        assert answer in {option.value for option in options}
        return answer

    return SetupConsole(select, lambda: SecretStr(secret), output.append)


@pytest.mark.parametrize("provider", ["openai", "deepseek", "anthropic"])
def test_noninteractive_environment_forwarding_and_noop(
    tmp_path: Path, provider: str
) -> None:
    verifier = AsyncMock()
    environment = {
        "HOME": str(tmp_path),
        f"{provider.upper()}_API_KEY": "fixture-environment",
        "OPENAI_BASE_URL": "https://fixture.invalid/v1",
        "UNRELATED_SECRET": "not-forwarded",
    }
    output = io.StringIO()
    kwargs = dict(
        environment=environment, catalog=catalog, verifier=verifier, output=output
    )
    assert main(arguments(provider), **kwargs) == 0
    runtime = tmp_path / ".hybro"
    assert (runtime / "auth.json").exists()
    call_config, credential, endpoint = verifier.await_args.args
    assert call_config == config(provider)
    assert isinstance(credential, ResolvedCredential)
    assert credential.source == "environment"
    assert credential.credential.api_key.get_secret_value() == "fixture-environment"
    assert endpoint == (
        environment["OPENAI_BASE_URL"] if provider == "openai" else None
    )
    before = (runtime / "config.json").stat().st_mtime_ns
    assert main(arguments(provider), **kwargs) == 0
    assert (runtime / "config.json").stat().st_mtime_ns == before
    assert verifier.await_count == 2
    assert "source: environment" in output.getvalue()
    assert "Unchanged" in output.getvalue()
    assert "restart backend with HYBRO_HOME" in output.getvalue()
    assert "fixture-environment" not in output.getvalue()


def test_noninteractive_image_flag_is_optional(tmp_path: Path) -> None:
    verifier = AsyncMock()
    assert (
        main(
            arguments()[:-2],
            environment={"HOME": str(tmp_path), "OPENAI_API_KEY": "fixture-env"},
            catalog=catalog,
            verifier=verifier,
        )
        == 0
    )
    assert verifier.await_args.args[0].models.image is None
    verifier.assert_awaited_once()


def test_image_selection_reports_unverified_access(tmp_path: Path) -> None:
    verifier = AsyncMock()
    output = io.StringIO()
    assert (
        main(
            arguments()[:-1] + ["fixture-image"],
            environment={"HOME": str(tmp_path), "OPENAI_API_KEY": "fixture-env"},
            catalog=catalog,
            verifier=verifier,
            output=output,
        )
        == 0
    )
    verifier.assert_awaited_once()
    assert verifier.await_args.args[0].models.image == "fixture-image"
    assert "image API access was not verified" in output.getvalue()
    assert "hybro start --recreate" in output.getvalue()


def test_setup_help_does_not_offer_apply_or_embedding(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as result:
        main(["--help"])
    assert result.value.code == 0
    help_text = capsys.readouterr().out
    assert "--image-model" in help_text
    assert "--apply" not in help_text and "--embedding" not in help_text
    assert "ChatGPT/Codex browser login" in " ".join(help_text.split())


def test_interactive_hidden_secret_and_model_selection(tmp_path: Path) -> None:
    output: list[str] = []
    verifier = AsyncMock()
    assert (
        main(
            [],
            environment={"HOME": str(tmp_path)},
            catalog=catalog,
            verifier=verifier,
            console=console(
                ["openai", "api_key", "fixture-text-2", "fixture-image"], output
            ),
        )
        == 0
    )
    state = RuntimeConfigStore(tmp_path / ".hybro").load({})
    assert state.config.models.text == "fixture-text-2"
    assert state.config.models.image == "fixture-image"
    assert (
        state.authentication.credential.api_key.get_secret_value() == "fixture-hidden"
    )
    assert "fixture-hidden" not in "\n".join(output)
    assert "Image model (optional): None - no image generation, fixture-image" in output
    assert any(
        "source: stored" in line and "pending verification and save" in line
        for line in output
    )


def test_interactive_no_images_skips_menu_and_reuses_stored(
    tmp_path: Path,
) -> None:
    store = RuntimeConfigStore(tmp_path / "runtime")
    store.save(config("anthropic"), key("anthropic"), {})
    output: list[str] = []
    verifier = AsyncMock()
    interactive = console(["anthropic", "api_key", "", "none"], output)
    interactive = SetupConsole(
        interactive.select,
        lambda: pytest.fail("must reuse stored key"),
        interactive.write,
    )
    assert (
        main(
            [],
            environment={"HYBRO_HOME": str(store.home)},
            catalog=catalog,
            verifier=verifier,
            console=interactive,
        )
        == 0
    )
    assert not any(line.startswith("Image model (optional):") for line in output)
    assert any("Image model: None" in line for line in output)
    assert verifier.await_args.args[1].credential == key("anthropic")
    assert "Unchanged; no files rewritten." in output


@pytest.mark.parametrize("stored_auth", ["api_key"])
def test_selected_provider_source_conflict_before_verification(
    tmp_path: Path, stored_auth: str
) -> None:
    store = RuntimeConfigStore(tmp_path / ".hybro")
    credential = (
        key() if stored_auth == "api_key" else oauth_credential(expires_at=2000000000.0)
    )
    store.save(config(auth=stored_auth), credential, {})
    original = (store.home / "auth.json").read_bytes()
    verifier = AsyncMock()
    error = io.StringIO()
    assert (
        main(
            arguments(),
            environment={"HOME": str(tmp_path), "OPENAI_API_KEY": "fixture-env"},
            catalog=catalog,
            verifier=verifier,
            error_output=error,
        )
        == 1
    )
    assert "remove one" in error.getvalue()
    verifier.assert_not_awaited()
    assert (store.home / "auth.json").read_bytes() == original


@pytest.mark.parametrize("new_source", ["environment", "stored"])
def test_provider_switch_replaces_old_identity(tmp_path: Path, new_source: str) -> None:
    store = RuntimeConfigStore(tmp_path / ".hybro")
    store.save(config(), key(), {})
    environment = {
        "HOME": str(tmp_path),
        "OPENAI_API_KEY": "irrelevant-old-provider-env",
    }
    output: list[str] = []
    if new_source == "environment":
        environment["DEEPSEEK_API_KEY"] = "fixture-new-env"
        argv = arguments("deepseek")
    else:
        argv = []
    assert (
        main(
            argv,
            environment=environment,
            catalog=catalog,
            verifier=AsyncMock(),
            console=console(["deepseek", "api_key", "", "none"], output),
        )
        == 0
    )
    state = store.load(environment)
    assert state.config.provider.id == "deepseek"
    assert state.authentication.source == "stored"
    assert (store.home / "auth.json").exists()


@pytest.mark.parametrize("existing", [False, True])
def test_verification_failure_redacted_and_never_saved(
    tmp_path: Path, existing: bool
) -> None:
    store = RuntimeConfigStore(tmp_path / ".hybro")
    if existing:
        store.save(config("deepseek"), key("deepseek"), {})
    before = (
        {
            path.name: path.read_bytes()
            for path in store.home.glob("*")
            if path.name != ".runtime.lock"
        }
        if existing
        else {}
    )
    verifier = AsyncMock(
        side_effect=RuntimeError(
            "fixture-secret https://user:password@fixture.invalid provider raw body"
        )
    )
    error = io.StringIO()
    assert (
        main(
            arguments(),
            environment={"HOME": str(tmp_path), "OPENAI_API_KEY": "fixture-secret"},
            catalog=catalog,
            verifier=verifier,
            error_output=error,
        )
        == 1
    )
    assert "verification failed" in error.getvalue()
    assert not any(
        value in error.getvalue()
        for value in ("fixture-secret", "password", "raw body")
    )
    assert {
        path.name: path.read_bytes()
        for path in store.home.glob("*")
        if path.name != ".runtime.lock"
    } == before


@pytest.mark.parametrize("failure", [KeyboardInterrupt, asyncio.CancelledError])
def test_cancellation_during_verification_does_not_save(
    tmp_path: Path, failure: type[BaseException]
) -> None:
    error = io.StringIO()
    assert (
        main(
            arguments(),
            environment={"HOME": str(tmp_path), "OPENAI_API_KEY": "fixture-env"},
            catalog=catalog,
            verifier=AsyncMock(side_effect=failure),
            error_output=error,
        )
        == 130
    )
    assert "canceled" in error.getvalue()
    assert not (tmp_path / ".hybro" / "auth.json").exists()
    assert not (tmp_path / ".hybro" / "config.json").exists()


@pytest.mark.parametrize("failure", [KeyboardInterrupt, EOFError])
def test_cancel_hidden_input(tmp_path: Path, failure: type[BaseException]) -> None:
    def cancel() -> SecretStr:
        raise failure

    verifier = AsyncMock()
    output: list[str] = []
    terminal = console(["openai", "api_key", "", "none"], output)
    assert (
        main(
            [],
            environment={"HOME": str(tmp_path)},
            catalog=catalog,
            verifier=verifier,
            console=SetupConsole(terminal.select, cancel, terminal.write),
        )
        == 130
    )
    verifier.assert_not_awaited()
    assert not (tmp_path / ".hybro" / "auth.json").exists()


@pytest.mark.parametrize(
    "argv",
    [
        ["--apply"],
        ["--provider", "openai", "--auth", "oauth"],
        ["--provider", "deepseek", "--auth", "oauth"],
        ["--non-interactive"],
        arguments() + ["--api-key", "fixture-argv-secret"],
        arguments() + ["--provider", "fixture-argv-secret"],
        arguments() + ["--text-model", "fixture-argv-secret"],
        arguments("anthropic")[:-1] + ["fixture-image"],
    ],
)
def test_invalid_or_unimplemented_request_fails_closed(
    tmp_path: Path, argv: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Partial interactive requests must not attempt a real /dev/tty during tests.
    monkeypatch.setattr(
        "llm_gateway.setup_cli._terminal_console",
        Mock(side_effect=SetupError("Fixture has no terminal.")),
    )
    error = io.StringIO()
    verifier = AsyncMock()
    assert (
        main(
            argv,
            environment={"HOME": str(tmp_path), "OPENAI_API_KEY": "fixture-env"},
            catalog=catalog,
            verifier=verifier,
            error_output=error,
        )
        == 1
    )
    assert "fixture-argv-secret" not in error.getvalue()
    verifier.assert_not_awaited()
    assert not (tmp_path / ".hybro" / "config.json").exists()


@pytest.mark.parametrize("stored", [False, True])
def test_noninteractive_requires_environment_key_even_with_stored(
    tmp_path: Path, stored: bool
) -> None:
    if stored:
        RuntimeConfigStore(tmp_path / ".hybro").save(config(), key(), {})
    verifier = AsyncMock()
    error = io.StringIO()
    assert (
        main(
            arguments(),
            environment={"HOME": str(tmp_path)},
            catalog=catalog,
            verifier=verifier,
            error_output=error,
        )
        == 1
    )
    assert "requires the selected environment key" in error.getvalue()
    verifier.assert_not_awaited()


def test_ineligible_model_entrypoint_has_no_side_effects(tmp_path: Path) -> None:
    error = io.StringIO()
    assert (
        main(
            arguments("anthropic"),
            environment={"HOME": str(tmp_path)},
            error_output=error,
        )
        == 1
    )
    assert "Selected model is not eligible" in error.getvalue()
    assert not (tmp_path / ".hybro").exists()


def test_entrypoint_help_from_outside_repository(tmp_path: Path) -> None:
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment["HOME"] = str(tmp_path)
    backend = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            shutil.which("uv") or "uv",
            "run",
            "--no-sync",
            "--frozen",
            "--project",
            str(backend),
            "--python",
            "3.12",
            "python",
            "-m",
            "llm_gateway.setup_cli",
            "--help",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Configure gateway text" in result.stdout
    assert not (tmp_path / ".hybro").exists()


def test_entrypoint_import_does_not_import_application() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import llm_gateway.setup_cli; assert 'main' not in sys.modules; assert 'container' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_catalog_failure_is_redacted(tmp_path: Path) -> None:
    def unavailable(provider: RuntimeProvider) -> ModelChoices:
        raise RuntimeError("fixture-secret")

    verifier = AsyncMock()
    error = io.StringIO()
    assert (
        main(
            arguments(),
            environment={"HOME": str(tmp_path)},
            catalog=unavailable,
            verifier=verifier,
            error_output=error,
        )
        == 1
    )
    assert "fixture-secret" not in error.getvalue()
    assert "Cannot load eligible models" in error.getvalue()
    verifier.assert_not_awaited()


def test_credential_changed_during_verification_is_not_overwritten(
    tmp_path: Path,
) -> None:
    store = RuntimeConfigStore(tmp_path / ".hybro")

    async def racing_verifier(*args: object) -> None:
        store.save(config(), key("openai", "fixture-concurrent"), {})

    error = io.StringIO()
    assert (
        main(
            arguments(),
            environment={"HOME": str(tmp_path), "OPENAI_API_KEY": "fixture-env"},
            catalog=catalog,
            verifier=racing_verifier,
            error_output=error,
        )
        == 1
    )
    assert "changed during setup; rerun setup" in error.getvalue()
    assert store.load({}).authentication.credential == key(
        "openai", "fixture-concurrent"
    )


def test_terminal_reads_tty_instead_of_piped_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = TerminalReader()
    mock_keyboard(monkeypatch, b"\x1b[B\r\r")
    writer = io.StringIO()
    opened: list[tuple[str, str]] = []

    def open_tty(path: str, mode: str = "r", *, encoding: str) -> io.StringIO:
        opened.append((path, mode))
        return reader if mode == "r" else writer

    def get_secret(prompt: str, *, stream: io.StringIO) -> str:
        assert stream is writer
        return "fixture-hidden"

    monkeypatch.setattr("builtins.open", open_tty)
    monkeypatch.setattr(getpass, "getpass", get_secret)
    monkeypatch.setattr(sys, "stdin", io.StringIO("not-a-terminal-secret\n"))
    with _terminal_console(io.StringIO()) as terminal:
        assert (
            terminal.select(
                "Provider",
                (SetupOption("openai", "OpenAI"), SetupOption("deepseek", "DeepSeek")),
            )
            == "deepseek"
        )
        assert terminal.read_secret().get_secret_value() == "fixture-hidden"
        assert "fixture-hidden" not in writer.getvalue()
        terminal.pause()
        assert "Press Enter to return" in writer.getvalue()
        with pytest.raises(EOFError):
            terminal.select("Provider", (SetupOption("openai", "OpenAI"),))
    assert opened == [("/dev/tty", "r"), ("/dev/tty", "w")]
    assert reader.closed and writer.closed


def test_terminal_refuses_echoing_getpass_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("builtins.open", lambda *args, **kwargs: io.StringIO())

    def get_secret(*args: object, **kwargs: object) -> str:
        warnings.warn(
            "Password input may be echoed.", getpass.GetPassWarning, stacklevel=2
        )
        pytest.fail("must stop before fallback reads the secret")

    monkeypatch.setattr(getpass, "getpass", get_secret)
    with _terminal_console(io.StringIO()) as terminal:
        with pytest.raises(SetupError, match="Cannot read a hidden API key"):
            terminal.read_secret()


def test_without_terminal_requires_noninteractive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def unavailable(*args: object, **kwargs: object) -> None:
        raise OSError("fixture-sensitive-error")

    monkeypatch.setattr("builtins.open", unavailable)
    error = io.StringIO()
    assert (
        main(
            [],
            environment={"HOME": str(tmp_path)},
            catalog=catalog,
            verifier=AsyncMock(),
            error_output=error,
        )
        == 1
    )
    assert "use --non-interactive" in error.getvalue()
    assert "fixture-sensitive-error" not in error.getvalue()
    assert not (tmp_path / ".hybro").exists()


@pytest.mark.parametrize("home", ["relative", ""])
def test_relative_runtime_home_rejected(tmp_path: Path, home: str) -> None:
    verifier = AsyncMock()
    error = io.StringIO()
    assert (
        main(
            arguments(),
            environment={"HOME": str(tmp_path), "HYBRO_HOME": home},
            catalog=catalog,
            verifier=verifier,
            error_output=error,
        )
        == 1
    )
    assert "must be absolute" in error.getvalue()
    verifier.assert_not_awaited()


def test_blank_secret_not_verified_or_saved(tmp_path: Path) -> None:
    verifier = AsyncMock()
    error = io.StringIO()
    assert (
        main(
            [],
            environment={"HOME": str(tmp_path)},
            catalog=catalog,
            verifier=verifier,
            console=console(["openai", "api_key", "", ""], [], secret=" "),
            error_output=error,
        )
        == 1
    )
    assert "Invalid setup selection or credential" in error.getvalue()
    verifier.assert_not_awaited()
    assert not (tmp_path / ".hybro" / "auth.json").exists()


def test_no_eligible_text_model_fails_before_auth(tmp_path: Path) -> None:
    verifier = AsyncMock()
    error = io.StringIO()
    assert (
        main(
            arguments(),
            environment={"HOME": str(tmp_path)},
            catalog=lambda provider: ModelChoices(()),
            verifier=verifier,
            error_output=error,
        )
        == 1
    )
    assert "No eligible text models" in error.getvalue()
    verifier.assert_not_awaited()
    assert not (tmp_path / ".hybro").exists()


@pytest.mark.parametrize(
    "endpoint",
    [
        "malformed",
        "file:///fixture-secret",
        "https:///fixture-secret",
        "https://user:fixture-secret@fixture.invalid/custom/v1",
        "https://fixture.invalid:bad/fixture-secret",
        "https://bad host/fixture-secret",
        "https://[invalid/fixture-secret",
        "https://fixture.invalid\n/fixture-secret",
        "http:fixture-secret",
        "https://@fixture.invalid/fixture-secret",
        r"https://fixture.invalid\fixture-secret",
    ],
)
def test_invalid_endpoint_fails_without_echo_or_verification(
    tmp_path: Path, endpoint: str
) -> None:
    verifier = AsyncMock()
    error = io.StringIO()
    assert (
        main(
            arguments(),
            environment={
                "HOME": str(tmp_path),
                "OPENAI_API_KEY": "fixture-key",
                "OPENAI_BASE_URL": endpoint,
            },
            catalog=catalog,
            verifier=verifier,
            error_output=error,
        )
        == 1
    )
    assert "Invalid OPENAI_BASE_URL" in error.getvalue()
    assert endpoint not in error.getvalue()
    assert "fixture-secret" not in error.getvalue()
    verifier.assert_not_awaited()
    assert not (tmp_path / ".hybro" / "config.json").exists()
    assert not (tmp_path / ".hybro" / "auth.json").exists()


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://fixture.invalid/custom/provider/v1/",
        "http://localhost:8080/custom/v1",
        "http://127.0.0.1:8080/fixture%20path/v1",
        "http://[::1]:8080/custom/v1",
        None,
    ],
)
def test_valid_endpoint_preserves_custom_path(
    tmp_path: Path, endpoint: str | None
) -> None:
    verifier = AsyncMock()
    environment = {"HOME": str(tmp_path), "OPENAI_API_KEY": "fixture-key"}
    if endpoint is not None:
        environment["OPENAI_BASE_URL"] = endpoint
    assert (
        main(arguments(), environment=environment, catalog=catalog, verifier=verifier)
        == 0
    )
    assert verifier.await_args.args[2] == endpoint


# Real signals run in a child so pytest's signal handling is never modified.
# This behavior harness deliberately runs from backend; outside-cwd packaging
# has its own strict subprocess test above without source-path injection.
_SIGNAL_CHILD = r"""
import asyncio
import signal
import sys
import time
from pathlib import Path
from pydantic import SecretStr
from common.config.runtime_config import ApiKeyCredential, RuntimeConfig
from common.config.runtime_store import RuntimeConfigStore
from llm_gateway.setup_cli import SetupConsole, main
from llm_gateway.setup_service import ModelChoices

stage, directory = sys.argv[1:]
home = Path(directory)
ready = home / "ready"
release = home / "release"
store = RuntimeConfigStore(home / ".hybro")
old_config = RuntimeConfig.model_validate({"provider": {"id": "deepseek", "auth": "api_key"}, "models": {"text": "fixture-old"}})
old_key = ApiKeyCredential(provider="deepseek", api_key=SecretStr("fixture-old-key"))
store.save(old_config, old_key, {})

def pause():
    ready.touch()
    while not release.exists():
        time.sleep(0.01)

def read_secret():
    if stage == "secret":
        pause()
    return SecretStr("fixture-new-key")

async def verify(*args):
    (home / "verified").touch()
    if stage in ("verify", "verify_suppressed", "verify_cleanup_error"):
        ready.touch()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            if stage == "verify_cleanup_error":
                raise RuntimeError("fixture-secret cleanup failed")
            if stage != "verify_suppressed":
                raise
    if stage == "verify_return":
        pause()

original = RuntimeConfigStore._replace
first_config = True

def replace(self, name, data):
    global first_config
    if stage == "commit_before_first_write" and name == "auth.json":
        pause()
    if stage == "rollback" and name == "config.json" and first_config:
        first_config = False
        pause()
        raise OSError("fixture write failure")
    original(self, name, data)
    if (stage == "commit_auth" and name == "auth.json") or (stage == "commit_config" and name == "config.json"):
        pause()

RuntimeConfigStore._replace = replace
previous = signal.getsignal(signal.SIGINT)
result = main(
    ["--provider", "openai", "--auth", "api_key", "--text-model", "fixture-new", "--image-model", "none"],
    environment={"HOME": str(home)}, catalog=lambda provider: ModelChoices(("fixture-new",)),
    verifier=verify, console=SetupConsole(lambda title, options: options[0].value, read_secret, print),
)
assert signal.getsignal(signal.SIGINT) == previous
raise SystemExit(result)
"""


@pytest.mark.parametrize(
    "stage",
    [
        "secret",
        "verify",
        "verify_suppressed",
        "verify_cleanup_error",
        "verify_return",
        "commit_before_first_write",
        "commit_auth",
        "commit_config",
        "rollback",
    ],
)
def test_real_sigint_respects_commit_boundary(tmp_path: Path, stage: str) -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", _SIGNAL_CHILD, stage, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not (tmp_path / "ready").exists():
            if process.poll() is not None or time.monotonic() >= deadline:
                pytest.fail("Signal test child did not reach the target window")
            time.sleep(0.01)
        process.send_signal(signal.SIGINT)
        (tmp_path / "release").touch()
        stdout, stderr = process.communicate(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
    state = RuntimeConfigStore(tmp_path / ".hybro").load({})
    if stage.startswith("commit_"):
        assert process.returncode == 0, stderr
        assert state.config.models.text == "fixture-new"
        assert (
            state.authentication.credential.api_key.get_secret_value()
            == "fixture-new-key"
        )
        assert "Saved config.json and auth.json." in stdout
        assert "canceled" not in stdout + stderr
    else:
        assert state.config.models.text == "fixture-old"
        assert (
            state.authentication.credential.api_key.get_secret_value()
            == "fixture-old-key"
        )
        assert "Saved config.json" not in stdout
        if stage == "rollback":
            assert process.returncode == 1, stderr
            assert "previous files restored" in stderr
            assert "canceled" not in stdout + stderr
        else:
            assert process.returncode == 130, stderr
            assert "Setup canceled." in stderr
            assert "verification failed" not in stdout + stderr
    assert "fixture-secret" not in stdout + stderr
    assert (tmp_path / "verified").exists() == (stage != "secret")
    assert "fixture-new-key" not in stdout + stderr
    assert "fixture-old-key" not in stdout + stderr
