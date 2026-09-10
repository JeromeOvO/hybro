"""Dependency-free Hybro cube frames and frontend-aligned terminal colors."""

from __future__ import annotations

import math
from functools import lru_cache
from itertools import product

# frontend/src/app/globals.css: dark primary and primary-foreground.
SELECTED_STYLE = "48;2;51;255;255;38;2;15;17;26"
LOGO_WIDTH = 14
LOGO_HEIGHT = 6
FRAME_COUNT = 80
FRAME_INTERVAL = 0.1


def _line(
    pixels: set[tuple[int, int]], a: tuple[float, float], b: tuple[float, float]
) -> None:
    steps = max(1, math.ceil(max(abs(b[0] - a[0]), abs(b[1] - a[1])) * 2))
    for i in range(steps + 1):
        x = round(a[0] + (b[0] - a[0]) * i / steps)
        y = round(a[1] + (b[1] - a[1]) * i / steps)
        if 0 <= x < LOGO_WIDTH * 2 and 0 <= y < LOGO_HEIGHT * 4:
            pixels.add((x, y))


def _cube_layers(frame: int) -> tuple[tuple[tuple[float, float], ...], ...]:
    """Fixed projection, complementary scales: the inner/outer roles exchange."""
    phase = (frame % FRAME_COUNT) * math.tau / FRAME_COUNT
    offset = 0.27 * math.cos(phase)
    layers = []
    for scale in (0.73 - offset, 0.73 + offset):
        vertices = []
        for x, y, z in product((-1, 1), repeat=3):
            horizontal = (x + z) / math.sqrt(2)
            depth = (z - x) / math.sqrt(2)
            vertical = y * math.cos(0.6) - depth * math.sin(0.6)
            vertices.append(
                (13.5 + horizontal * 6.5 * scale, 11.5 + vertical * 6.5 * scale)
            )
        layers.append(tuple(vertices))
    return tuple(layers)


@lru_cache(maxsize=FRAME_COUNT)
def logo_frame(frame: int) -> tuple[tuple[str, str], ...]:
    """Nested blue cubes expand/contract through each other without rotating."""
    phase = (frame % FRAME_COUNT) * math.tau / FRAME_COUNT
    pixels: set[tuple[int, int]] = set()
    inner, outer = _cube_layers(frame)
    for vertices in (inner, outer):
        for i, vertex in enumerate(vertices):
            for bit in (1, 2, 4):
                if i < (i ^ bit):
                    _line(pixels, vertex, vertices[i ^ bit])
    for a, b in zip(inner, outer, strict=True):
        _line(pixels, a, b)
    dots = (
        (0, 0, 1),
        (0, 1, 2),
        (0, 2, 4),
        (1, 0, 8),
        (1, 1, 16),
        (1, 2, 32),
        (0, 3, 64),
        (1, 3, 128),
    )
    rows = []
    pulse = 0.88 + 0.12 * math.cos(phase * 2)
    for row in range(LOGO_HEIGHT):
        text = ""
        for column in range(LOGO_WIDTH):
            value = sum(
                bit for dx, dy, bit in dots if (column * 2 + dx, row * 4 + dy) in pixels
            )
            text += chr(0x2800 + value) if value else " "
        blend = row / (LOGO_HEIGHT - 1)
        red = round((51 + 7 * blend) * pulse)
        green = round((255 - 125 * blend) * pulse)
        blue = round((255 - 30 * blend) * pulse)
        rows.append((text, f"38;2;{red};{green};{blue}"))
    return tuple(rows)
