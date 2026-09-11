"""Mock terminal syscalls; never allocate a PTY or access a real terminal."""

from __future__ import annotations

import io
from unittest.mock import Mock

import pytest

from llm_gateway import setup_terminal


class TerminalReader(io.StringIO):
    def fileno(self) -> int:
        return 71


def mock_keyboard(monkeypatch: pytest.MonkeyPatch, keys: bytes) -> tuple[Mock, Mock]:
    pending = list(keys)

    def read(fd: int, size: int) -> bytes:
        assert (fd, size) == (71, 1)
        return bytes([pending.pop(0)]) if pending else b""

    def ready(
        readers: list[int], writers: list[int], errors: list[int], timeout: float
    ):
        assert (readers, writers, errors) == ([71], [], [])
        assert timeout in (0.15, setup_terminal.FRAME_INTERVAL)
        # EOF is readable during the menu's animation poll; Escape lookahead times out.
        return (
            readers if pending or timeout == setup_terminal.FRAME_INTERVAL else [],
            [],
            [],
        )

    raw, restore = Mock(), Mock()
    monkeypatch.setattr(
        setup_terminal.os,
        "get_terminal_size",
        lambda fd=71: setup_terminal.os.terminal_size((80, 24)),
    )
    monkeypatch.setattr(setup_terminal.os, "read", read)
    monkeypatch.setattr(setup_terminal.select, "select", ready)
    monkeypatch.setattr(setup_terminal.termios, "tcgetattr", lambda fd: ["original"])
    monkeypatch.setattr(setup_terminal.tty, "setraw", raw)
    monkeypatch.setattr(setup_terminal.termios, "tcsetattr", restore)
    return raw, restore
