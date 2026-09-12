"""Resolve the Compose stack this CLI orchestrates, with or without a checkout.

A source checkout runs ``docker-compose.yml`` in place, where ``--build`` and
regeneration from ``default_agents/agents.yaml`` are meaningful. Any other
install (frozen binary or npm package) has no checkout, so it runs the published
Compose file bundled beside the CLI. Both files pin the same Compose project
name, so container identities do not depend on where the CLI is installed --
which is what lets a single-file bundle extract somewhere new on every run.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from common.config.runtime_config import RuntimeConfigurationError

DEV_COMPOSE = "docker-compose.yml"
RELEASE_COMPOSE = "docker-compose.release.yml"
MANIFEST = "default_agents/agents.yaml"
RENDERER = "default_agents/render_compose.py"
HELP = "scripts/hybro-help.txt"
VERSION = "VERSION"


@dataclass(frozen=True, slots=True)
class Stack:
    """Compose project directory and the file that defines it."""

    root: Path
    compose: Path
    checkout: bool


def _frozen_root() -> Path | None:
    """Extraction directory of a frozen CLI, when this is one."""
    bundled = getattr(sys, "_MEIPASS", None)
    return Path(bundled) if bundled is not None else None


def checkout_root() -> Path | None:
    """Repository root when this CLI runs from a source checkout, else ``None``."""
    if _frozen_root() is not None:
        return None
    root = Path(__file__).resolve().parents[1]
    if (root / DEV_COMPOSE).is_file() and (root / MANIFEST).is_file():
        return root
    return None


def data_file(name: str) -> Path:
    """A CLI data file, from the checkout or from the frozen CLI's bundle."""
    base = checkout_root() or _frozen_root()
    if base is None:
        raise RuntimeConfigurationError("CLI data is unavailable; reinstall hybro.")
    path = base / name
    if not path.is_file():
        raise RuntimeConfigurationError(f"CLI data file is missing: {name}.")
    return path


def version() -> str:
    """The version this CLI runs; it also selects the images the stack runs."""
    value = data_file(VERSION).read_text(encoding="utf-8").strip()
    if not value:
        raise RuntimeConfigurationError("CLI version file is empty; reinstall hybro.")
    return value


def resolve() -> Stack:
    """The stack to run: a source checkout in place, or the published file."""
    root = checkout_root()
    if root is not None:
        return Stack(root=root, compose=root / DEV_COMPOSE, checkout=True)
    compose = data_file(RELEASE_COMPOSE)
    return Stack(root=compose.parent, compose=compose, checkout=False)
