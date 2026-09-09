"""Keyboard-only host CLI menu; delegate lifecycle effects to scripts/hybro."""

from __future__ import annotations

import os
import subprocess
import sys
import termios
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from llm_gateway.setup_cli import SetupConsole, _terminal_console
from llm_gateway.setup_service import SetupError
from llm_gateway.setup_terminal import SetupOption

_ACTIONS = (
    SetupOption("setup", "Configure provider and models"),
    SetupOption("start", "Start services"),
    SetupOption("logs", "Follow logs (Ctrl-C exits)"),
    SetupOption("more", "More operations"),
)
_MORE_ACTIONS = (
    SetupOption("status", "Show service status"),
    SetupOption("restart", "Restart services"),
    SetupOption("stop", "Stop services (keep containers)"),
    SetupOption("build", "Build images and start services"),
    SetupOption("recreate", "Recreate containers with existing images"),
    SetupOption("build_recreate", "Build images and recreate containers"),
    SetupOption("down", "Remove containers and network (keep named volumes)"),
    SetupOption("help", "Show command help"),
    SetupOption("back", "Back"),
)
_START_MODES = {
    "build": ("--build",),
    "recreate": ("--recreate",),
    "build_recreate": ("--build", "--recreate"),
}


def _run_command(arguments: tuple[str, ...]) -> int:
    script = Path(__file__).resolve().parents[2] / "scripts" / "hybro"
    # No shell string, arbitrary command input, credential argument or new owner.
    return subprocess.run([os.fspath(script), *arguments], check=False).returncode


def _menu(console: SetupConsole, run: Callable[[tuple[str, ...]], int]) -> int:
    while True:
        action = console.select("Hybro", _ACTIONS)
        if action == "more":
            action = console.select("More operations", _MORE_ACTIONS)
            if action == "back":
                continue
        arguments = (
            ("start", *_START_MODES[action]) if action in _START_MODES else (action,)
        )
        if action in {"recreate", "build_recreate", "down"}:
            if (
                console.select(
                    "Confirm: hybro " + " ".join(arguments),
                    (
                        SetupOption("cancel", "Cancel"),
                        SetupOption("run", "Run command"),
                    ),
                )
                == "cancel"
            ):
                continue
        result = run(arguments)
        console.write("Command finished." if result == 0 else "Command failed.")
        console.pause()


def main(
    *,
    console: SetupConsole | None = None,
    run: Callable[[tuple[str, ...]], int] = _run_command,
    output: TextIO | None = None,
) -> int:
    output = output if output is not None else sys.stdout
    try:
        if console is not None:
            return _menu(console, run)
        with _terminal_console(output) as terminal:
            return _menu(terminal, run)
    except (KeyboardInterrupt, EOFError):
        print("Hybro menu canceled.", file=output)
        return 130
    except (SetupError, OSError, termios.error):
        print(
            "Cannot use TUI; check the terminal or use an explicit subcommand.",
            file=output,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
