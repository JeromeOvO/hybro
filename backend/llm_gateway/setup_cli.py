"""CLI setup for gateway text and optional image models.

Text verification calls the selected Provider; image selection is validated locally.
Tests inject or mock network access. No Agents or application bootstrap is invoked.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import signal
import sys
import termios
import warnings
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import TextIO

from pydantic import SecretStr, ValidationError

from llm_gateway.runtime_config import (
    RuntimeConfig,
    RuntimeConfigurationError,
    RuntimeModels,
    RuntimeProvider,
)
from llm_gateway.runtime_store import (
    RuntimeConfigStore,
    parse_config,
    prepare_setup_directory,
    runtime_home,
)
from llm_gateway.setup_service import (
    CredentialVerifier,
    ModelCatalog,
    SetupError,
    SetupService,
    _PreparedAuthentication,
)
from llm_gateway.setup_terminal import (
    SetupOption,
    SetupScreen,
    select_option,
    wait_for_enter,
)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse's default echoes arbitrary argv, including misplaced secrets.
        raise SetupError("Invalid setup arguments; run with --help for usage.")


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="hybro setup",
        description=(
            "Configure gateway text and optional image models. "
            "Verification sends one text request (API billing or subscription quota). "
            "OpenAI OAuth uses ChatGPT/Codex browser login on this computer. "
            "Choose Provider/auth, authenticate, then choose models, verify and save. "
            "OAuth ignores OPENAI_API_KEY; it remains available for embeddings. "
            "Image access is not probed; OAuth has no image generation models. "
            "Interactive choices use Up/Down arrows and Enter (Esc/Ctrl-C cancels). "
            "Setup tightens an existing runtime directory owned by you to 0700; "
            "symlinks, non-directories and other owners are refused."
        ),
        allow_abbrev=False,
    )
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--provider", choices=("openai", "deepseek", "anthropic"))
    parser.add_argument("--auth", choices=("api_key", "oauth"))
    parser.add_argument("--text-model", help="Explicit eligible text model ID")
    parser.add_argument(
        "--image-model", help="Explicit eligible image model ID or none"
    )
    return parser


@dataclass(frozen=True, slots=True)
class SetupConsole:
    select: Callable[[str | SetupScreen, tuple[SetupOption, ...]], str]
    read_secret: Callable[[], SecretStr]
    write: Callable[[str], None]
    # Only the lifecycle menu pauses; injected/headless setup consoles need not.
    pause: Callable[[], None] = lambda: None


@contextmanager
def _terminal_console(
    output: TextIO, *, screen: bool = False
) -> Iterator[SetupConsole]:
    with ExitStack() as stack:
        try:
            reader = stack.enter_context(open("/dev/tty", encoding="utf-8"))
            writer = stack.enter_context(open("/dev/tty", "w", encoding="utf-8"))
        except OSError:
            raise SetupError(
                "Interactive setup requires a terminal; use --non-interactive."
            ) from None

        def read_secret() -> SecretStr:
            # Never accept getpass's echoing fallback when terminal control fails.
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                try:
                    return SecretStr(
                        getpass.getpass("API key (hidden): ", stream=writer)
                    )
                except getpass.GetPassWarning:
                    raise SetupError(
                        "Cannot read a hidden API key from this terminal."
                    ) from None

        def select(title: str | SetupScreen, options: tuple[SetupOption, ...]) -> str:
            return select_option(reader, writer, title, options, screen=screen)

        try:
            if screen:
                writer.write("\x1b[?1049h")
                writer.flush()
            yield SetupConsole(
                select,
                read_secret,
                lambda message: print(
                    message, file=writer if screen else output, flush=True
                ),
                lambda: wait_for_enter(reader, writer, screen=screen),
            )
        finally:
            if screen:
                writer.write("\x1b[0m\x1b[?25h\x1b[?1049l")
                writer.flush()


def _no_input(title: str | SetupScreen, options: tuple[SetupOption, ...]) -> str:
    raise SetupError("Non-interactive setup cannot prompt for input.")


def _no_secret() -> SecretStr:
    raise SetupError("Non-interactive setup cannot prompt for a secret.")


def _selection(
    args: argparse.Namespace,
    service: SetupService,
    console: SetupConsole,
    environment: Mapping[str, str],
    initial: RuntimeConfig | None = None,
) -> tuple[RuntimeConfig, _PreparedAuthentication]:
    provider_id = args.provider or console.select(
        "Provider",
        tuple(
            SetupOption(
                value,
                label,
                current=initial is not None and initial.provider.id == value,
            )
            for value, label in (
                ("openai", "OpenAI"),
                ("deepseek", "DeepSeek"),
                ("anthropic", "Anthropic"),
            )
        ),
    )
    methods = (
        SetupOption(
            "api_key",
            "API key",
            current=initial is not None and initial.provider.auth == "api_key",
        ),
    )
    if provider_id == "openai":
        methods += (
            SetupOption(
                "oauth",
                "ChatGPT/Codex OAuth (browser login)",
                current=initial is not None and initial.provider.auth == "oauth",
            ),
        )
    provider = RuntimeProvider.model_validate(
        {
            "id": provider_id,
            "auth": args.auth or console.select("Authentication", methods),
        }
    )
    choices = service.choices(provider)
    # Reject explicit ineligible flags locally, before acquiring authentication.
    if (args.text_model is not None and args.text_model not in choices.text) or (
        args.image_model not in (None, "none") and args.image_model not in choices.image
    ):
        raise SetupError("Selected model is not eligible for this Provider/auth.")
    prepared = service.authenticate(
        provider,
        environment,
        non_interactive=args.non_interactive,
        read_secret=console.read_secret,
        report_source=console.write,
    )
    text = args.text_model
    if text is None:
        text = console.select(
            "Text model",
            tuple(
                SetupOption(
                    model,
                    model + (" (recommended)" if index == 0 else ""),
                    current=initial is not None and initial.models.text == model,
                )
                for index, model in enumerate(choices.text)
            ),
        )
    image = args.image_model
    if image is None:
        if not args.non_interactive and choices.image:
            image = console.select(
                "Image model (optional)",
                (
                    SetupOption(
                        "none",
                        "None - no image generation",
                        current=initial is not None and initial.models.image is None,
                    ),
                )
                + tuple(
                    SetupOption(
                        model,
                        model,
                        current=initial is not None and initial.models.image == model,
                    )
                    for model in choices.image
                ),
            )
        else:
            image = "none"
    return RuntimeConfig(
        provider=provider,
        models=RuntimeModels(text=text, image=None if image == "none" else image),
    ), prepared


def _read_current_config(service: SetupService) -> RuntimeConfig | None:
    data = service.store._read("config.yaml")
    if data is None:
        return None
    config = parse_config(data)
    # Only catalog IDs may reach the terminal, not arbitrary file contents.
    service.choices(config.provider).validate(config)
    return config


def _show_current_config(service: SetupService, console: SetupConsole) -> None:
    """Display config only, never resolve credentials or imply backend activation."""
    try:
        config = _read_current_config(service)
        if config is None:
            console.write("Current configuration: none saved.")
            return
    except (OSError, RuntimeConfigurationError, SetupError):
        console.write("Current configuration: invalid or unreadable; configure below.")
        return
    provider = {"openai": "OpenAI", "deepseek": "DeepSeek", "anthropic": "Anthropic"}[
        config.provider.id
    ]
    auth = "ChatGPT/Codex OAuth" if config.provider.auth == "oauth" else "API key"
    console.write(
        "Current configuration (saved locally):\n"
        f"  Provider: {provider}\n"
        f"  Authentication: {auth}\n"
        f"  Text model: {config.models.text}\n"
        f"  Image model: {config.models.image or 'None'}\n"
        "Credential validity and backend activation are not checked here."
    )


def _execute(
    args: argparse.Namespace,
    environment: Mapping[str, str],
    service: SetupService,
    console: SetupConsole,
    cleanup: ExitStack,
) -> None:
    if prepare_setup_directory(service.store.home):
        console.write(
            "Corrected runtime directory permissions to 0700; contents unchanged."
        )
    if not args.non_interactive:
        _show_current_config(service, console)
    console.write(
        "Setup verifies text with one Provider request (API billing or subscription quota); no image calls."
    )
    config, prepared = _selection(args, service, console, environment)
    _save_selection(config, prepared, service, console, cleanup)


def _save_selection(
    config: RuntimeConfig,
    prepared: _PreparedAuthentication,
    service: SetupService,
    console: SetupConsole,
    cleanup: ExitStack,
) -> None:
    def begin_commit() -> None:
        # This is the commit boundary: before it SIGINT cancels without saving;
        # after it SIGINT cannot interrupt the locked write/rollback or reporting.
        # The caller restores this as its save scope exits (also inside a panel).
        previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
        cleanup.callback(signal.signal, signal.SIGINT, previous)

    console.write("Verifying selected text model (one request)...")
    result = service.save(
        config,
        prepared,
        begin_commit=begin_commit,
    )
    console.write(
        "Saved config.yaml/auth source."
        if result.changed
        else "Unchanged; no files rewritten."
    )
    if config.models.image is not None:
        console.write(
            "Image model saved; image API access was not verified (no image calls)."
        )
    console.write("Not applied: restart backend with HYBRO_HOME set to this directory.")
    console.write(
        "Docker: run docker compose up -d --build --no-deps --force-recreate backend "
        "from the repository, using the same HYBRO_HOME and credential environment."
    )


def _run(
    args: argparse.Namespace,
    environment: Mapping[str, str],
    catalog: ModelCatalog | None,
    verifier: CredentialVerifier | None,
    console: SetupConsole | None,
    output: TextIO,
    cleanup: ExitStack,
) -> None:
    if args.auth == "oauth" and args.non_interactive:
        raise SetupError("OAuth requires interactive setup on your browser's host.")
    if args.non_interactive and any(
        value is None for value in (args.provider, args.auth, args.text_model)
    ):
        raise SetupError(
            "Non-interactive setup requires Provider, auth and text model."
        )
    if catalog is None and verifier is None:
        from llm_gateway.catalog import model_choices
        from llm_gateway.setup_bindings import verify_selection

        catalog, verifier = model_choices, verify_selection
    elif catalog is None or verifier is None:
        raise SetupError("Catalog and verifier must be supplied together.")
    service = SetupService(
        RuntimeConfigStore(runtime_home(environment)), catalog, verifier
    )
    if console is not None:
        _execute(args, environment, service, console, cleanup)
    elif args.non_interactive:
        _execute(
            args,
            environment,
            service,
            SetupConsole(
                _no_input, _no_secret, lambda message: print(message, file=output)
            ),
            cleanup,
        )
    else:
        with _terminal_console(output) as terminal:
            _execute(args, environment, service, terminal, cleanup)


def main(
    argv: Sequence[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    catalog: ModelCatalog | None = None,
    verifier: CredentialVerifier | None = None,
    console: SetupConsole | None = None,
    output: TextIO | None = None,
    error_output: TextIO | None = None,
) -> int:
    output = sys.stdout if output is None else output
    error_output = sys.stderr if error_output is None else error_output
    with ExitStack() as cleanup:
        try:
            args = _parser().parse_args(argv)
            # Host-only boundary: the lifecycle caller will resolve shell/root-.env
            # precedence. Do not import application Settings or read .env here.
            environment = dict(os.environ if environment is None else environment)
            _run(args, environment, catalog, verifier, console, output, cleanup)
        except (KeyboardInterrupt, EOFError, asyncio.CancelledError):
            print("Setup canceled.", file=error_output)
            return 130
        except (SetupError, RuntimeConfigurationError) as exc:
            print(str(exc), file=error_output)
            return 1
        except ValidationError:
            print(
                "Invalid setup selection or credential; no configuration saved.",
                file=error_output,
            )
            return 1
        except (OSError, termios.error):
            print(
                "Setup I/O failed; check the private runtime directory and terminal.",
                file=error_output,
            )
            return 1
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
