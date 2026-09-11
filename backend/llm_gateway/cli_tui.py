"""Settings and services pages; effects remain in setup and scripts/hybro."""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
import termios
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TextIO

from pydantic import ValidationError

from common.config.runtime_config import RuntimeConfigurationError
from common.config.runtime_store import RuntimeConfigStore, runtime_home
from llm_gateway.setup_cli import SetupConsole, _terminal_console
from llm_gateway.setup_panel import SetupPanel
from llm_gateway.setup_service import SetupError, SetupService
from llm_gateway.setup_terminal import SelectionCancelled, SetupOption, SetupScreen

_SERVICE_ACTIONS = (
    SetupOption("start", "Start services"),
    SetupOption("stop", "Stop services"),
    SetupOption(
        "apply",
        "Reload configuration",
        hint="Reload saved configuration: rebuild and recreate. Services will be interrupted.",
    ),
    SetupOption("logs", "View logs"),
    SetupOption("models", "Model configuration [Tab]", shortcut=b"\t"),
)

# Host capability injected by the CLI entry point; the gateway never imports it.
StatusReader = Callable[[], list[dict[str, object]]]


def _read_status() -> list[dict[str, object]]:
    """Standalone default; the host CLI injects its Compose reader instead."""
    raise RuntimeConfigurationError(
        "Compose status is available through the hybro CLI entry point"
    )


def _status_snapshot(
    read: StatusReader | None = None,
) -> tuple[str, list[dict[str, object]]]:
    try:
        rows = (read or _read_status)()
        running = sum(row.get("State") == "running" for row in rows)
        return f"{running} running" if rows else "No containers", rows
    except (OSError, ValueError, RuntimeConfigurationError, subprocess.SubprocessError):
        return "Status unavailable: check Docker", []


def _service_states(rows: list[dict[str, object]]) -> tuple[tuple[str, bool], ...]:
    states: dict[str, bool] = {}
    for row in rows:
        name = row.get("Service") or row.get("Name")
        if not isinstance(name, str) or not re.fullmatch(
            r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", name
        ):
            continue
        if name in {"mongo-setup", "registrar"}:
            continue
        states[name] = states.get(name, True) and row.get("State") == "running"
    return tuple(sorted(states.items()))


def _logs_page(
    console: SetupConsole,
    run: Callable[[tuple[str, ...]], int],
    read: StatusReader | None = None,
) -> None:
    while True:
        status, rows = _status_snapshot(read)
        names = sorted(
            {
                row["Name"]
                for row in rows
                if isinstance(row.get("Name"), str)
                and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", row["Name"])
            }
        )
        try:
            choice = console.select(
                SetupScreen(
                    "Container logs",
                    status=status,
                    notice="Last 100 lines, then follow. While following, Ctrl-C returns here.",
                ),
                (
                    SetupOption("all", "All containers"),
                    *(SetupOption(name, name) for name in names),
                    SetupOption("back", "Back"),
                ),
            )
        except SelectionCancelled:
            return
        if choice == "back":
            return
        arguments = (
            ("logs", "--tail", "100")
            if choice == "all"
            else ("logs", "--container", choice)
        )
        try:
            result = run(arguments)
        except KeyboardInterrupt:
            result = 130
        if result != 130:
            console.write(
                "Log stream ended."
                if result == 0
                else "Logs unavailable; check Docker and the selected container."
            )
            try:
                console.pause()
            except SelectionCancelled:
                pass


def _run_command(arguments: tuple[str, ...]) -> int:
    script = Path(__file__).resolve().parents[2] / "scripts" / "hybro"
    return subprocess.run([os.fspath(script), *arguments], check=False).returncode


def _service_command(
    console: SetupConsole, run: Callable[[tuple[str, ...]], int], action: str
) -> str:
    arguments = ("start", "--build", "--recreate") if action == "apply" else (action,)
    if action == "apply":
        if (
            console.select(
                "Reload saved configuration? Services will be interrupted.",
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
    console: SetupConsole,
    run: Callable[[tuple[str, ...]], int],
    focus: str,
    read: StatusReader | None = None,
) -> str:
    from dataclasses import replace

    notice = ""
    while True:
        status, rows = _status_snapshot(read)
        try:
            action = console.select(
                SetupScreen(
                    "Hybro",
                    tab="services",
                    status=status if not rows else "",
                    service_states=_service_states(rows),
                    notice="\n".join(
                        filter(
                            None,
                            (
                                notice,
                                "More commands: hybro --help. Status refreshes on return.",
                            ),
                        )
                    ),
                ),
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
            if action == "logs":
                _logs_page(console, run, read)
            else:
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
    read: StatusReader | None = None,
) -> int:
    panel = SetupPanel(service, console, environment)
    service_focus = "start"
    while True:
        try:
            action = console.select(panel.title(), panel.options())
        except SelectionCancelled:
            if panel.can_exit():
                return 0
            continue
        if action == "services":
            service_focus = _services_page(console, run, service_focus, read)
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
    status: StatusReader | None = None,
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
            return _menu(console, run, service, environment, status)
        with _terminal_console(output, screen=True) as terminal:
            return _menu(terminal, run, service, environment, status)
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
