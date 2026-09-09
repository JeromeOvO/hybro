"""Settings and services pages; effects remain in setup and scripts/hybro."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import termios
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TextIO

from pydantic import ValidationError

from llm_gateway.runtime_config import RuntimeConfigurationError
from llm_gateway.runtime_store import RuntimeConfigStore, runtime_home
from llm_gateway.setup_cli import SetupConsole, _terminal_console
from llm_gateway.setup_panel import SetupPanel
from llm_gateway.setup_service import SetupError, SetupService
from llm_gateway.setup_terminal import SelectionCancelled, SetupOption, SetupScreen

_SERVICE_ACTIONS = (
    SetupOption("status", "Show service status"),
    SetupOption("logs", "Follow logs (Ctrl-C exits)"),
    SetupOption("start", "Start services"),
    SetupOption("restart", "Restart services"),
    SetupOption("stop", "Stop services (keep containers)"),
    SetupOption("build", "Build images and start services"),
    SetupOption("recreate", "Recreate containers with existing images"),
    SetupOption("build_recreate", "Build images and recreate containers"),
    SetupOption("down", "Remove containers and network (keep named volumes)"),
    SetupOption("help", "Show command help"),
    SetupOption("models", "Model configuration [Tab]", shortcut=b"\t"),
)
_START_MODES = {
    "build": ("--build",),
    "recreate": ("--recreate",),
    "build_recreate": ("--build", "--recreate"),
}


def _run_command(arguments: tuple[str, ...]) -> int:
    script = Path(__file__).resolve().parents[2] / "scripts" / "hybro"
    return subprocess.run([os.fspath(script), *arguments], check=False).returncode


def _service_command(
    console: SetupConsole, run: Callable[[tuple[str, ...]], int], action: str
) -> str:
    arguments = (
        ("start", *_START_MODES[action]) if action in _START_MODES else (action,)
    )
    if action in {"recreate", "build_recreate", "down"}:
        if (
            console.select(
                "Confirm: hybro " + " ".join(arguments),
                (SetupOption("cancel", "Cancel"), SetupOption("run", "Run command")),
            )
            == "cancel"
        ):
            return "Canceled; services unchanged."
    result = run(arguments)
    notice = (
        "Command canceled."
        if result == 130
        else "Command finished."
        if result == 0
        else "Command failed."
    )
    console.write(notice)
    try:
        console.pause()
    except SelectionCancelled:
        pass
    return notice


def _services_page(
    console: SetupConsole, run: Callable[[tuple[str, ...]], int], focus: str
) -> str:
    from dataclasses import replace

    notice = "No service operation runs automatically."
    while True:
        try:
            action = console.select(
                SetupScreen("Hybro", tab="services", notice=notice),
                tuple(
                    replace(option, current=option.value == focus)
                    for option in _SERVICE_ACTIONS
                ),
            )
        except SelectionCancelled:
            return focus
        if action == "models":
            return focus
        focus = action
        try:
            notice = _service_command(console, run, action)
        except SelectionCancelled:
            continue
        except (SetupError, OSError, termios.error):
            notice = "Service command failed; check Docker and the terminal."


def _menu(
    console: SetupConsole,
    run: Callable[[tuple[str, ...]], int],
    service: SetupService,
    environment: Mapping[str, str],
) -> int:
    panel = SetupPanel(service, console, environment)
    service_focus = "status"
    while True:
        try:
            action = console.select(panel.title(), panel.options())
        except SelectionCancelled:
            if panel.can_exit():
                return 0
            continue
        if action == "services":
            service_focus = _services_page(console, run, service_focus)
            continue
        try:
            panel.edit(action)
        except SelectionCancelled:
            # Close a picker/confirmation, leaving its parent and draft intact.
            continue
        except (SetupError, RuntimeConfigurationError) as exc:
            panel.notice = str(exc)
        except (OSError, termios.error, ValidationError):
            panel.notice = (
                "Operation failed; check the private configuration and terminal."
            )


def main(
    *,
    console: SetupConsole | None = None,
    run: Callable[[tuple[str, ...]], int] = _run_command,
    output: TextIO | None = None,
    environment: Mapping[str, str] | None = None,
    service: SetupService | None = None,
) -> int:
    output = output if output is not None else sys.stdout
    try:
        environment = dict(os.environ if environment is None else environment)
        if service is None:
            from llm_gateway.catalog import model_choices
            from llm_gateway.setup_bindings import verify_selection

            service = SetupService(
                RuntimeConfigStore(runtime_home(environment)),
                model_choices,
                verify_selection,
            )
        if console is not None:
            return _menu(console, run, service, environment)
        with _terminal_console(output, screen=True) as terminal:
            return _menu(terminal, run, service, environment)
    except (KeyboardInterrupt, EOFError, asyncio.CancelledError):
        print("Hybro closed; unsaved changes discarded.", file=output)
        return 130
    except (SetupError, RuntimeConfigurationError, OSError, termios.error):
        print(
            "Cannot use TUI; check configuration/terminal or use an explicit subcommand.",
            file=output,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
