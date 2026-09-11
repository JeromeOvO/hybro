#!/usr/bin/env python3
"""Generate docker-compose.release.yml from docker-compose.yml.

The development stack builds images from this checkout. A released stack runs
published images instead, so every service that has a `build:` block is replaced
by an `image:` reference to the registry build of that same service. Everything
else -- ports, volumes, healthchecks, the environment contract -- is carried over
unchanged, which keeps the two files from drifting.

The CLI supplies HYBRO_STACK_TAG (its own version) and optionally
HYBRO_IMAGE_REGISTRY, so a released stack always runs the images published for
the CLI that started it.

Usage:
    python default_agents/render_release.py           # rewrite docker-compose.release.yml
    python default_agents/render_release.py --check    # exit 1 if out of sync (CI)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SOURCE_COMPOSE = ROOT / "docker-compose.yml"
RELEASE_COMPOSE = ROOT / "docker-compose.release.yml"

REGISTRY = "${HYBRO_IMAGE_REGISTRY:-ghcr.io/hybroai}"
TAG = "${HYBRO_STACK_TAG:?the hybro CLI sets the stack version}"

HEADER = """\
# Generated from docker-compose.yml by default_agents/render_release.py.
# Do not edit by hand: edit docker-compose.yml and regenerate.
#
# Differences from the development stack:
#   - every service runs a published image instead of a local build
#   - image tags come from the hybro CLI that starts this stack
# Build arguments are absent, so a released stack never rebuilds anything.
"""


def image_for(service: str) -> str:
    """Registry reference for a service's published image."""
    return f"{REGISTRY}/hybro-{service}:{TAG}"


def _dumper() -> type[yaml.SafeDumper]:
    """A dumper that ignores aliases, so every service is fully expanded."""

    class Dumper(yaml.SafeDumper):
        def ignore_aliases(self, data: object) -> bool:
            return True

    return Dumper


def render(source_text: str) -> str:
    """Return the release stack for a development stack document."""
    document = yaml.safe_load(source_text)
    if not isinstance(document, dict) or not isinstance(document.get("services"), dict):
        raise ValueError("docker-compose.yml has no services mapping")
    # The anchor only exists to share the development build block between agents.
    document.pop("x-agent", None)
    for name, service in document["services"].items():
        if not isinstance(service, dict):
            raise ValueError(f"service '{name}' is not a mapping")
        if "build" not in service:
            continue
        service.pop("build")
        service.pop("pull_policy", None)
        service["image"] = image_for(name)
    body = yaml.dump(
        document,
        Dumper=_dumper(),
        sort_keys=False,
        default_flow_style=False,
        width=4096,
    )
    return HEADER + body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if docker-compose.release.yml is out of sync.",
    )
    args = parser.parse_args(argv)

    expected = render(SOURCE_COMPOSE.read_text(encoding="utf-8"))
    current = (
        RELEASE_COMPOSE.read_text(encoding="utf-8") if RELEASE_COMPOSE.is_file() else ""
    )
    if args.check:
        if current != expected:
            print(
                "docker-compose.release.yml is out of sync with docker-compose.yml.\n"
                "Run: python default_agents/render_release.py",
                file=sys.stderr,
            )
            return 1
        print("docker-compose.release.yml is in sync with docker-compose.yml.")
        return 0
    if current == expected:
        print("docker-compose.release.yml already up to date.")
        return 0
    RELEASE_COMPOSE.write_text(expected, encoding="utf-8")
    print(f"Wrote {RELEASE_COMPOSE}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
