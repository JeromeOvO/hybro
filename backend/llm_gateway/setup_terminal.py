"""Small POSIX arrow-key selector for host setup; never reads standard input."""

from __future__ import annotations

import io
import os
import select
import termios
import textwrap
import time
import tty
from collections.abc import Callable
from dataclasses import dataclass
from typing import TextIO

from llm_gateway.tui_brand import (
    FRAME_COUNT,
    FRAME_INTERVAL,
    LOGO_HEIGHT,
    LOGO_WIDTH,
    SELECTED_STYLE,
    logo_frame,
)


@dataclass(frozen=True, slots=True)
class SetupOption:
    value: str
    label: str
    current: bool = False
    shortcut: bytes | None = None
    hint: str = ""


@dataclass(frozen=True, slots=True)
class SetupScreen:
    """Presentation only; page state and actions stay with the caller."""

    title: str
    tab: str = ""
    status: str = ""
    notice: str = ""
    service_states: tuple[tuple[str, bool], ...] = ()


class SelectionCancelled(KeyboardInterrupt):
    """Escape closes a local picker; Ctrl-C still exits the application."""


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
            raise SelectionCancelled
        sequence += os.read(fd, 1)
    if sequence not in (b"[A", b"[B", b"OA", b"OB"):
        raise SelectionCancelled
    return b"\x1b" + sequence


def wait_for_enter(reader: TextIO, writer: TextIO, *, screen: bool = False) -> None:
    """Keep command output visible without another option menu."""
    fd = reader.fileno()
    previous = termios.tcgetattr(fd)
    try:
        tty.setraw(fd, termios.TCSANOW)
        writer.write(
            "Press Enter or Esc to return; Ctrl-C exits.\r\n"
            if screen
            else "Press Enter to return; Esc/Ctrl-C exits.\r\n"
        )
        writer.flush()
        while _key(fd) not in (b"\r", b"\n"):
            pass
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, previous)


def _choose_option(
    fd: int,
    options: tuple[SetupOption, ...],
    selected: int,
    draw: Callable[[int], None],
    *,
    animate: bool = False,
) -> str:
    paused = False
    while True:
        draw(selected)
        if (
            animate
            and not paused
            and not select.select([fd], [], [], FRAME_INTERVAL)[0]
        ):
            continue
        key = _key(fd)
        if animate and key == b" ":
            paused = not paused
            continue
        shortcut = next(
            (option.value for option in options if option.shortcut == key), None
        )
        if shortcut is not None:
            return shortcut
        if key in (b"\r", b"\n"):
            return options[selected].value
        step = {b"\x1b[A": -1, b"\x1bOA": -1, b"\x1b[B": 1, b"\x1bOB": 1}.get(key, 0)
        selected = (selected + step) % len(options)


def _append_service_status(
    lines: list[tuple[str, str]],
    states: tuple[tuple[str, bool], ...],
    width: int,
    limit: int,
    spacer: list[tuple[str, str]],
) -> list[tuple[int, int, str, str]]:
    """Pack status items into a row, wrapping only at service boundaries."""
    highlights: list[tuple[int, int, str, str]] = []
    if not states:
        return highlights
    text = ""
    for name, active in states:
        state = "active" if active else "inactive"
        name = name[: max(0, width - len(state) - 2)]
        item = f"{name}: {state}" if name else state
        if text and len(text) + 3 + len(item) > width:
            lines.append((text, "0"))
            text = ""
        if text:
            text += "   "
        highlights.append(
            (
                len(lines),
                len(text) + len(item) - len(state),
                state,
                "32" if active else "31",
            )
        )
        text += item
    if text:
        lines.append((text, "0"))
    if len(lines) > limit:
        del lines[limit:]
        lines[-1] = ("More services: widen terminal", "2")
        highlights = [span for span in highlights if span[0] < limit - 1]
    lines.extend(spacer)
    return highlights


def _draw_screen(
    writer: TextIO,
    heading: str | SetupScreen,
    options: tuple[SetupOption, ...],
    selected: int,
    columns: int,
    rows: int,
) -> None:
    """A bounded, centered terminal panel using the user's terminal palette."""
    page = heading if isinstance(heading, SetupScreen) else SetupScreen(heading)
    brand = rows >= 24 and columns >= 40
    logo_height = LOGO_HEIGHT + 1 if brand else 0
    width, height = min(72, max(1, columns - 4)), max(1, rows - 1 - logo_height)
    if page.service_states:
        natural_width = sum(
            len(name) + (8 if active else 10) for name, active in page.service_states
        ) + 3 * (len(page.service_states) - 1)
        width = min(max(width, natural_width), max(1, columns - 4))
    compact = height < 18
    spacer = [] if compact else [("", "0")]
    # Tab destinations remain keyboard-selectable, but are drawn in the tab bar.
    body = [
        (i, option)
        for i, option in enumerate(options)
        if not (page.tab and option.shortcut)
    ]
    focus = min(selected, len(body) - 1)
    help_text = (
        "Up/Down select  Enter confirm  Tab page  Esc back  Ctrl-C quit"
        if page.tab
        else "Up/Down select  Enter confirm  Esc back  Ctrl-C quit"
    )
    help_text = (
        "Up/Down  Enter  "
        + ("Tab page  " if page.tab else "")
        + "Esc back  Ctrl-C quit  Space pause"
        if brand
        else help_text
    )
    if width < 60:
        help_text = "Up/Down Enter Tab Esc ^C" if page.tab else "Up/Down Enter Esc ^C"
    notice = "\n".join(filter(None, (options[selected].hint, page.notice)))
    notes = [
        part for line in notice.splitlines() for part in textwrap.wrap(line, width)
    ]
    note_limit = max(0, min(5, height - 8))
    if len(notes) > note_limit and note_limit:
        notes[note_limit - 1] = (
            notes[note_limit - 1][: max(0, width - 3)] + "..."[:width]
        )
    notes = notes[:note_limit]
    header = page.title[:width]
    if page.status and len(header) + len(page.status) + 2 <= width:
        header = header.ljust(width - len(page.status)) + page.status
    elif page.status:
        notes = [page.status, *notes][: max(0, height - 8)]
    lines = [(header, "1")]
    if page.tab:
        lines.append(("Models     Services", "0"))
    lines.extend(spacer)
    highlights = _append_service_status(
        lines,
        page.service_states,
        width,
        len(lines)
        + max(1, height - len(lines) - len(notes) - (4 if not compact else 2)),
        spacer,
    )
    gaps = 0 if compact else sum(bool(option.hint) for _, option in body)
    visible = min(
        len(body),
        max(1, height - len(lines) - len(notes) - (3 if not compact else 1) - gaps),
    )
    start = max(0, min(focus - visible + 1, len(body) - visible))
    for index, option in body[start : start + visible]:
        if option.hint and not compact:
            lines.extend(spacer)
        current = " [current]" if option.current and not page.tab else ""
        label = ("> " if index == selected else "  ") + option.label + current
        lines.append((label, SELECTED_STYLE if index == selected else "0"))
    lines.extend(spacer)
    lines.extend((note, "2") for note in notes)
    lines.extend(spacer)
    counter = f" {focus + 1}/{len(body)}" if visible < len(body) else ""
    lines.append((help_text[: max(0, width - len(counter))] + counter, "2"))
    if height < 5:
        lines = [(options[selected].label, SELECTED_STYLE), ("Enter Esc ^C", "0")]
        highlights = []
    frame = io.StringIO()
    _paint_screen(
        frame,
        lines[:height],
        width,
        (columns, rows),
        page.tab if height >= 5 else "",
        options[selected].value,
        highlights,
        brand=brand,
    )
    # One write per frame avoids exposing the clear/paint intermediate state.
    writer.write(frame.getvalue())
    writer.flush()


def _paint_screen(
    writer: TextIO,
    lines: list[tuple[str, str]],
    width: int,
    size: tuple[int, int],
    active_tab: str,
    focused_tab: str,
    highlights: list[tuple[int, int, str, str]] | None = None,
    *,
    brand: bool = False,
) -> None:
    columns, rows = size
    logo = (
        logo_frame(int(time.monotonic() / FRAME_INTERVAL) % FRAME_COUNT)
        if brand
        else ()
    )
    logo_height = len(logo) + 1 if logo else 0
    panel_height = len(lines) + logo_height
    top, left = (
        max(1, (rows - panel_height) // 2 + 1),
        max(1, (columns - width) // 2 + 1),
    )
    writer.write("\x1b[2J\x1b[H")
    for offset, (text, color) in enumerate(logo):
        writer.write(
            f"\x1b[{top + offset};{(columns - LOGO_WIDTH) // 2 + 1}H\x1b[{color}m{text}\x1b[0m"
        )
    top += logo_height
    for offset, (text, style) in enumerate(lines):
        clipped = (
            text[: width - 3] + "..."
            if len(text) > width and width >= 3
            else text[:width]
        )
        writer.write(
            f"\x1b[{top + offset};{left}H\x1b[{style}m{clipped.ljust(width)}\x1b[0m"
        )
    for row, column, text, color in highlights or ():
        if row < len(lines) and column + len(text) <= width:
            writer.write(f"\x1b[{top + row};{left + column}H\x1b[{color}m{text}\x1b[0m")
    if active_tab:
        for tab, label, offset in (
            ("models", "Models", 0),
            ("services", "Services", 11),
        ):
            style = (
                SELECTED_STYLE
                if focused_tab == tab
                else "1;4"
                if active_tab == tab
                else "2"
            )
            if offset + len(label) <= width:
                writer.write(
                    f"\x1b[{top + 1};{left + offset}H\x1b[{style}m{label}\x1b[0m"
                )
    # Leave ordinary authentication/service output below the panel, not in a row.
    writer.write(f"\x1b[{top + len(lines)};1H")
    writer.flush()


def _terminal_size(fd: int) -> tuple[int, int]:
    try:
        return os.get_terminal_size(fd)
    except OSError:
        return 80, 24


def select_option(
    reader: TextIO,
    writer: TextIO,
    title: str | SetupScreen,
    options: tuple[SetupOption, ...],
    *,
    screen: bool = False,
) -> str:
    """Focus the current value, or the first option; never accept typed IDs."""
    fd = reader.fileno()
    previous = termios.tcgetattr(fd)
    initial = next((i for i, option in enumerate(options) if option.current), 0)
    columns, rows = _terminal_size(fd)
    width = max(1, columns - 1)
    headings = (title.title if isinstance(title, SetupScreen) else title).splitlines()
    visible = min(len(options), max(1, rows - len(headings) - 2))
    start = 0
    scrolls = visible < len(options)
    try:
        tty.setraw(fd, termios.TCSANOW)
        writer.write("\x1b[?25l")
        if screen:
            return _choose_option(
                fd,
                options,
                initial,
                lambda selected: _draw_screen(
                    writer, title, options, selected, *_terminal_size(fd)
                ),
                animate=True,
            )
        headings[-1] += " (Up/Down, Enter; Esc/Ctrl-C cancels)"
        for heading in headings:
            writer.write(heading[:width] + "\r\n")
        drawn = False

        def draw(selected: int) -> None:
            nonlocal start, drawn
            if drawn:
                writer.write(f"\x1b[{visible + int(scrolls)}A")
            drawn = True
            start = min(start, selected)
            start = max(start, selected - visible + 1)
            for index in range(start, start + visible):
                option = options[index]
                marker = ">" if index == selected else " "
                default = (
                    " (current)"
                    if option.current
                    else " (default)"
                    if index == initial
                    else ""
                )
                line = f"{marker} {option.label}{default}"
                writer.write(f"\r\x1b[2K{line[:width]}\r\n")
            if scrolls:
                line = f"Showing {start + 1}-{start + visible} of {len(options)}"
                writer.write(f"\r\x1b[2K{line[:width]}\r\n")
            writer.flush()

        return _choose_option(fd, options, initial, draw)
    finally:
        # Terminal restoration must still run if drawing or cursor cleanup fails.
        try:
            writer.write(("\x1b[0m" if screen else "") + "\x1b[?25h")
            writer.flush()
        finally:
            termios.tcsetattr(fd, termios.TCSANOW, previous)
