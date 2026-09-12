"""Report and apply CLI upgrades, which also move the deployed stack version.

The CLI runs the images published for its own version, so upgrading the CLI is
what upgrades the stack. How that happens depends on how the CLI was installed,
which is what `installation()` detects: an npm install can be replaced in place,
a release-archive install must be unpacked again, and a source checkout is
updated from Git.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from cli_stack import checkout_root, version
from common.config.runtime_config import RuntimeConfigurationError

PACKAGE = "@hybroai/cli"
REPOSITORY = "hybroai/hybro"
_NETWORK_TIMEOUT = 15

# How the CLI was installed. "checkout" is also what cli_stack reports for a
# source tree, so callers can compare against one spelling.
CHECKOUT = "checkout"
NPM = "npm"
ARCHIVE = "archive"


def installation() -> str:
    """Which channel installed this CLI, or `checkout` for a source tree."""
    if checkout_root() is not None:
        return CHECKOUT
    # npm runs the platform package's binary directly, so the path still names
    # the package: <prefix>/node_modules/@hybroai/cli-<platform>/hybro/hybro.
    parts = Path(sys.executable).resolve().parts
    for index, part in enumerate(parts[:-2]):
        if (
            part == "node_modules"
            and parts[index + 1] == "@hybroai"
            and parts[index + 2].startswith("cli")
        ):
            return NPM
    return ARCHIVE


def platform_name() -> str:
    """The release-asset suffix for this machine, e.g. `darwin-arm64`."""
    machine = platform.machine().lower()
    if machine in {"arm64", "aarch64"}:
        architecture = "arm64"
    elif machine in {"x86_64", "amd64"}:
        architecture = "x64"
    else:
        raise RuntimeConfigurationError(
            f"No prebuilt CLI for this processor ({machine}); build from source."
        )
    system = "darwin" if sys.platform == "darwin" else "linux"
    return f"{system}-{architecture}"


def _npm_latest() -> str:
    try:
        result = subprocess.run(
            ["npm", "view", PACKAGE, "version"],
            capture_output=True,
            text=True,
            timeout=_NETWORK_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise RuntimeConfigurationError(
            "Cannot run npm to check for a newer hybro."
        ) from None
    if result.returncode or not result.stdout.strip():
        raise RuntimeConfigurationError(
            "Cannot reach the npm registry; check the network and npm login."
        )
    return result.stdout.strip()


def _release_latest() -> str:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPOSITORY}/releases/latest",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "hybro-cli"},
    )
    try:
        with urllib.request.urlopen(request, timeout=_NETWORK_TIMEOUT) as response:
            document = json.load(response)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        raise RuntimeConfigurationError(
            "Cannot reach GitHub Releases; check the network."
        ) from None
    tag = document.get("tag_name") if isinstance(document, dict) else None
    if not isinstance(tag, str) or not tag.startswith("v"):
        raise RuntimeConfigurationError("GitHub returned an unexpected release.")
    return tag.removeprefix("v")


def latest() -> str:
    """The newest published version on this installation's channel."""
    return _npm_latest() if installation() == NPM else _release_latest()


def _numbered(value: str) -> tuple[int, int, int]:
    """Compare released versions only; there is no pre-release channel."""
    parts = value.split("-", 1)[0].split("+", 1)[0].split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise RuntimeConfigurationError(f"Unrecognized version: {value}.")
    return int(parts[0]), int(parts[1]), int(parts[2])


def _install_from_npm() -> None:
    result = subprocess.run(
        ["npm", "install", "--global", f"{PACKAGE}@latest"], check=False
    )
    if result.returncode:
        raise RuntimeConfigurationError("npm could not install the newer hybro.")


def _print_archive_steps(newest: str) -> None:
    asset = f"hybro-{newest}-{platform_name()}.tar.gz"
    base = f"https://github.com/{REPOSITORY}/releases/download/v{newest}"
    print(
        "This install came from a release archive, which hybro will not replace\n"
        "in place. To upgrade, download the newer build, verify it, unpack it,\n"
        "and run the unpacked executable:\n\n"
        f"  curl -fsSLO {base}/{asset}\n"
        f"  curl -fsSLO {base}/{asset}.sha256\n"
        f"  shasum -a 256 --check {asset}.sha256\n"
        f"  tar -xzf {asset}\n"
        f"  ./hybro/hybro --version\n\n"
        "The archive carries the Compose stack that matches its version, so\n"
        "replace the directory this CLI was installed into with `hybro/`."
    )


def run(*, check: bool) -> int:
    """Report, and unless ``check``, apply the newest published release.

    ``check`` exits non-zero when an upgrade is available, matching the other
    `--check` gates in this repository.
    """
    installed = installation()
    if installed == CHECKOUT:
        raise RuntimeConfigurationError(
            "This CLI runs from a source checkout; update it with git pull, then "
            "hybro start --build --recreate."
        )

    current = version()
    newest = latest()
    outdated = _numbered(newest) > _numbered(current)

    if check:
        print(
            f"hybro {current} is installed; {newest} is available."
            if outdated
            else f"hybro {current} is the newest release."
        )
        return 1 if outdated else 0

    if not outdated:
        print(f"hybro {current} is already the newest release; nothing to do.")
        return 0

    if installed == NPM:
        print(f"Installing hybro {newest} (currently {current}).")
        _install_from_npm()
        print(
            f"hybro {newest} installed. Run hybro start --recreate to move the "
            "containers onto the images published for it."
        )
        return 0

    _print_archive_steps(newest)
    print("\nThen run hybro start --recreate.")
    return 1
