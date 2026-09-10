"""Pure rendering and fake input; no terminal, daemon, credentials or threads."""

import io
import math
import re
from itertools import pairwise
from unittest.mock import Mock

import pytest

from llm_gateway import setup_terminal as terminal
from llm_gateway.tui_brand import (
    FRAME_COUNT,
    FRAME_INTERVAL,
    LOGO_HEIGHT,
    LOGO_WIDTH,
    SELECTED_STYLE,
    _cube_layers,
    logo_frame,
)
from tests.fakes.setup_terminal import TerminalReader, mock_keyboard


def test_cube_is_bounded_blue_and_loops():
    frames = [logo_frame(frame) for frame in range(FRAME_COUNT)]
    assert frames[0] == logo_frame(FRAME_COUNT)
    # Fixed-angle scaling has fewer raster silhouettes than rotation.
    assert len(set(frames)) >= 12
    for frame in frames:
        assert len(frame) == LOGO_HEIGHT
        for text, color in frame:
            assert len(text) == LOGO_WIDTH
            assert all(char == " " or 0x2800 <= ord(char) <= 0x28FF for char in text)
            assert color.startswith("38;2;")
            red, green, blue = map(int, color.split(";")[2:])
            assert 0 <= red < green <= blue <= 255
    assert logo_frame.cache_info().currsize <= FRAME_COUNT


def test_inner_expands_while_outer_contracts_then_exchanges_roles():
    def radius(vertices):
        return max(math.hypot(x - 13.5, y - 11.5) for x, y in vertices)

    first_half = [_cube_layers(frame) for frame in range(FRAME_COUNT // 2 + 1)]
    inner = [radius(layers[0]) for layers in first_half]
    outer = [radius(layers[1]) for layers in first_half]
    assert inner[0] < outer[0]
    assert all(a < b for a, b in pairwise(inner))
    assert all(a > b for a, b in pairwise(outer))
    assert inner[-1] == pytest.approx(outer[0])
    assert outer[-1] == pytest.approx(inner[0])
    assert inner[FRAME_COUNT // 4] == pytest.approx(outer[FRAME_COUNT // 4])
    for a, b in zip(inner, outer, strict=True):
        assert a + b == pytest.approx(inner[0] + outer[0])


def test_all_frames_keep_the_same_projection_and_uniform_scale():
    baseline = _cube_layers(0)
    for frame in range(FRAME_COUNT):
        for vertices, original in zip(_cube_layers(frame), baseline, strict=True):
            ratios = []
            assert len(vertices) == 8
            for (x, y), (bx, by) in zip(vertices, original, strict=True):
                assert 0 <= x < LOGO_WIDTH * 2 and 0 <= y < LOGO_HEIGHT * 4
                dx, dy, ox, oy = x - 13.5, y - 11.5, bx - 13.5, by - 11.5
                assert dx * oy - dy * ox == pytest.approx(0, abs=1e-10)
                assert dx * ox + dy * oy > 0
                ratios.append(math.hypot(dx, dy) / math.hypot(ox, oy))
            assert ratios == pytest.approx([ratios[0]] * 8)


def test_scaling_loop_has_no_reset_jump():
    assert _cube_layers(0) == _cube_layers(FRAME_COUNT)
    for previous, start in zip(
        _cube_layers(FRAME_COUNT - 1), _cube_layers(0), strict=True
    ):
        for a, b in zip(previous, start, strict=True):
            assert math.dist(a, b) < 0.02


@pytest.mark.parametrize("size", [(80, 24), (120, 32), (200, 40), (32, 10), (16, 5)])
def test_panel_is_centered_and_selection_matches_frontend(size):
    output = io.StringIO()
    terminal._draw_screen(
        output,
        terminal.SetupScreen("Hybro", tab="models"),
        (terminal.SetupOption("model", "Text model"),),
        0,
        *size,
    )
    spans = re.findall(
        r"\x1b\[(\d+);(\d+)H\x1b\[([\d;]+)m([^\x1b]*)\x1b\[0m", output.getvalue()
    )
    first = min(int(row) for row, _, _, _ in spans)
    last = max(int(row) for row, _, _, _ in spans)
    left = min(int(col) for _, col, _, _ in spans)
    right = max(int(col) + len(text) - 1 for _, col, _, text in spans)
    assert abs((first - 1) - (size[1] - last)) <= 1
    assert abs((left - 1) - (size[0] - right)) <= 1
    assert any(style == SELECTED_STYLE for _, _, style, _ in spans)
    assert "\x1b[7m" not in output.getvalue()
    assert SELECTED_STYLE == "48;2;51;255;255;38;2;15;17;26"


def test_frame_is_written_atomically():
    output = Mock()
    terminal._draw_screen(
        output, "Models", (terminal.SetupOption("a", "A"),), 0, 80, 24
    )
    output.write.assert_called_once()
    output.flush.assert_called_once()


def test_idle_animation_ticks_without_reading_input_or_dispatching_actions(monkeypatch):
    mock_keyboard(monkeypatch, b"\r")
    ready = Mock(side_effect=[([], [], []), ([], [], []), ([71], [], [])])
    monkeypatch.setattr(terminal.select, "select", ready)
    draw = Mock()
    assert (
        terminal._choose_option(
            71, (terminal.SetupOption("a", "A"),), 0, draw, animate=True
        )
        == "a"
    )
    assert draw.call_count == 3
    assert all(
        call.args == ([71], [], [], FRAME_INTERVAL) for call in ready.call_args_list
    )


def test_idle_resize_recenters_next_frame(monkeypatch):
    mock_keyboard(monkeypatch, b"\r")
    monkeypatch.setattr(
        terminal.os,
        "get_terminal_size",
        Mock(side_effect=[(80, 24), (80, 24), (120, 32)]),
    )
    monkeypatch.setattr(
        terminal.select, "select", Mock(side_effect=[([], [], []), ([71], [], [])])
    )
    output = io.StringIO()
    assert (
        terminal.select_option(
            TerminalReader(),
            output,
            "Models",
            (terminal.SetupOption("a", "A"),),
            screen=True,
        )
        == "a"
    )
    frames = output.getvalue().split("\x1b[2J\x1b[H")[1:]
    assert len(frames) == 2
    for frame, column in zip(frames, (34, 54), strict=True):
        logo_columns = re.findall(r"\x1b\[\d+;(\d+)H\x1b\[38;2;", frame)
        assert logo_columns == [str(column)] * LOGO_HEIGHT


@pytest.mark.parametrize("keys,polls", [(b" \r", 1), (b"  \r", 2)])
def test_space_pauses_and_resumes_idle_ticks(monkeypatch, keys, polls):
    mock_keyboard(monkeypatch, keys)
    ready = Mock(return_value=([71], [], []))
    monkeypatch.setattr(terminal.select, "select", ready)
    assert (
        terminal._choose_option(
            71, (terminal.SetupOption("a", "A"),), 0, Mock(), animate=True
        )
        == "a"
    )
    assert ready.call_count == polls


@pytest.mark.parametrize(
    "keys,error",
    [
        (b"\x03", KeyboardInterrupt),
        (b"", EOFError),
        (b"\x1b", terminal.SelectionCancelled),
    ],
)
def test_animated_screen_restores_terminal_on_exit(monkeypatch, keys, error):
    _, restore = mock_keyboard(monkeypatch, keys)
    output = io.StringIO()
    with pytest.raises(error):
        terminal.select_option(
            TerminalReader(),
            output,
            "Models",
            (terminal.SetupOption("a", "A"),),
            screen=True,
        )
    restore.assert_called_once()
    assert output.getvalue().endswith("\x1b[0m\x1b[?25h")
