#!/usr/bin/env python3
"""Generate packaging/frontend/public-config.json from the backend schema.

The published frontend image serves browser settings per request, but its
server-side API rewrite (`api_prefix`) and the backend URL are compiled into the
image at build time. This file is the projection used for that build, and it
carries the defaults a local install needs.

Writing it by hand would let the image's routing drift from what the CLI
produces for a default configuration, so this script derives it from the same
pydantic model the CLI uses and CI verifies the checked-in file.

Usage:
    python packaging/frontend/render_public_config.py           # rewrite the file
    python packaging/frontend/render_public_config.py --check    # exit 1 if stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
OUTPUT = Path(__file__).resolve().parent / "public-config.json"

sys.path.insert(0, str(BACKEND))

from common.config.loader import FrontendSettings, Settings  # noqa: E402


def projection() -> str:
    """The public projection the CLI builds for a default configuration."""
    public = FrontendSettings.model_validate({"api_prefix": Settings().api_prefix})
    return public.model_dump_json() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Exit non-zero if the file is stale."
    )
    args = parser.parse_args(argv)

    expected = projection()
    current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.is_file() else ""
    if args.check:
        if current != expected:
            print(
                f"{OUTPUT} is out of sync with the backend schema.\n"
                "Run: python packaging/frontend/render_public_config.py",
                file=sys.stderr,
            )
            return 1
        print(f"{OUTPUT} is in sync with the backend schema.")
        return 0
    if current == expected:
        print(f"{OUTPUT} already up to date.")
        return 0
    OUTPUT.write_text(expected, encoding="utf-8")
    print(f"Wrote {OUTPUT}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
