"""Small POSIX arrow-key selector for host setup; never reads standard input."""

from __future__ import annotations

import os
import select
import termios
import tty
from dataclasses import dataclass
from typing import TextIO


@dataclass(frozen=True, slots=True)
class SetupOption:
    value: str
    label: str


def _key(fd: int) -> bytes:
    key = os.read(fd, 1)
    if not key or key == b"\x04":
        raise EOFError
    if key == b"\x03":
        raise KeyboardInterrupt
    if key != b"\x1b":
        return key
    # Escape alone cancels. Bound the wait for each byte of an arrow sequence.
    sequence = b""
    for _ in range(2):
        if not select.select([fd], [], [], 0.15)[0]:
            raise KeyboardInterrupt
        sequence += os.read(fd, 1)
    if sequence not in (b"[A", b"[B", b"OA", b"OB"):
        raise KeyboardInterrupt
    return b"\x1b" + sequence


def wait_for_enter(reader: TextIO, writer: TextIO) -> None:
    """Keep command output visible without another option menu."""
    fd = reader.fileno()
    previous = termios.tcgetattr(fd)
    try:
        tty.setraw(fd, termios.TCSANOW)
        writer.write("Press Enter to return; Esc/Ctrl-C exits.\r\n")
        writer.flush()
        while _key(fd) not in (b"\r", b"\n"):
            pass
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, previous)


def select_option(
    reader: TextIO, writer: TextIO, title: str, options: tuple[SetupOption, ...]
) -> str:
    """The first option is the default; typed IDs are deliberately not accepted."""
    fd = reader.fileno()
    previous = termios.tcgetattr(fd)
    selected = 0
    try:
        columns, rows = os.get_terminal_size(fd)
    except OSError:
        columns, rows = 80, 24
    width = max(1, columns - 1)
    visible = min(len(options), max(1, rows - 3))
    start = 0
    scrolls = visible < len(options)
    try:
        tty.setraw(fd, termios.TCSANOW)
        writer.write("\x1b[?25l")
        heading = f"{title} (Up/Down, Enter; Esc/Ctrl-C cancels)"
        writer.write(heading[:width] + "\r\n")
        while True:
            start = min(start, selected)
            start = max(start, selected - visible + 1)
            for index in range(start, start + visible):
                option = options[index]
                marker = ">" if index == selected else " "
                default = " (default)" if index == 0 else ""
                line = f"{marker} {option.label}{default}"
                writer.write(f"\r\x1b[2K{line[:width]}\r\n")
            if scrolls:
                line = f"Showing {start + 1}-{start + visible} of {len(options)}"
                writer.write(f"\r\x1b[2K{line[:width]}\r\n")
            writer.flush()
            key = _key(fd)
            if key in (b"\r", b"\n"):
                return options[selected].value
            if key in (b"\x1b[A", b"\x1bOA"):
                selected = (selected - 1) % len(options)
            elif key in (b"\x1b[B", b"\x1bOB"):
                selected = (selected + 1) % len(options)
            writer.write(f"\x1b[{visible + int(scrolls)}A")
    finally:
        # Terminal restoration must still run if drawing or cursor cleanup fails.
        try:
            writer.write("\x1b[?25h")
            writer.flush()
        finally:
            termios.tcsetattr(fd, termios.TCSANOW, previous)
