"""Upgrade checks use fake package managers and fake registries only."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import cli_upgrade
from common.config.runtime_config import RuntimeConfigurationError


@pytest.fixture
def npm_install(monkeypatch):
    """Pretend npm installed the CLI, so its binary still sits in node_modules."""
    monkeypatch.setattr(cli_upgrade, "checkout_root", lambda: None)
    monkeypatch.setattr(
        sys,
        "executable",
        "/usr/local/lib/node_modules/@hybroai/cli-darwin-arm64/hybro/hybro",
    )
    return cli_upgrade.NPM


def test_installation_distinguishes_the_three_channels(monkeypatch):
    monkeypatch.setattr(cli_upgrade, "checkout_root", lambda: None)
    monkeypatch.setattr(sys, "executable", "/opt/hybro/hybro")
    assert cli_upgrade.installation() == cli_upgrade.ARCHIVE

    # The scope is its own path component, so the package is the next one.
    monkeypatch.setattr(
        sys,
        "executable",
        "/usr/local/lib/node_modules/@hybroai/cli-linux-x64/hybro/hybro",
    )
    assert cli_upgrade.installation() == cli_upgrade.NPM

    monkeypatch.setattr(
        sys, "executable", "/opt/npm/node_modules/@hybroai/cli/hybro/hybro"
    )
    assert cli_upgrade.installation() == cli_upgrade.NPM

    # An unrelated node_modules tree is not an npm install of this CLI.
    monkeypatch.setattr(sys, "executable", "/work/node_modules/tool/hybro")
    assert cli_upgrade.installation() == cli_upgrade.ARCHIVE

    # A checkout wins over the interpreter path: it is the developer channel.
    monkeypatch.setattr(cli_upgrade, "checkout_root", lambda: Path("/repo"))
    assert cli_upgrade.installation() == cli_upgrade.CHECKOUT


def test_check_reports_an_available_release(monkeypatch, capsys):
    monkeypatch.setattr(cli_upgrade, "installation", lambda: cli_upgrade.NPM)
    monkeypatch.setattr(cli_upgrade, "version", lambda: "0.2.15")
    monkeypatch.setattr(cli_upgrade, "latest", lambda: "0.3.0")

    assert cli_upgrade.run(check=True) == 1
    assert "0.3.0 is available" in capsys.readouterr().out


def test_check_reports_being_current(monkeypatch, capsys):
    monkeypatch.setattr(cli_upgrade, "installation", lambda: cli_upgrade.NPM)
    monkeypatch.setattr(cli_upgrade, "version", lambda: "0.3.0")
    monkeypatch.setattr(cli_upgrade, "latest", lambda: "0.3.0")

    assert cli_upgrade.run(check=True) == 0
    assert "newest release" in capsys.readouterr().out


def test_npm_install_is_replaced_in_place(monkeypatch, capsys):
    monkeypatch.setattr(cli_upgrade, "installation", lambda: cli_upgrade.NPM)
    monkeypatch.setattr(cli_upgrade, "version", lambda: "0.2.15")
    monkeypatch.setattr(cli_upgrade, "latest", lambda: "0.3.0")
    calls = []
    monkeypatch.setattr(
        cli_upgrade.subprocess,
        "run",
        lambda arguments, **kwargs: (
            calls.append(arguments) or SimpleNamespace(returncode=0)
        ),
    )

    assert cli_upgrade.run(check=False) == 0
    assert calls == [["npm", "install", "--global", "@hybroai/cli@latest"]]
    assert "--recreate" in capsys.readouterr().out


def test_current_install_does_not_reach_the_package_manager(monkeypatch, capsys):
    monkeypatch.setattr(cli_upgrade, "installation", lambda: cli_upgrade.NPM)
    monkeypatch.setattr(cli_upgrade, "version", lambda: "0.3.0")
    monkeypatch.setattr(cli_upgrade, "latest", lambda: "0.3.0")
    monkeypatch.setattr(
        cli_upgrade.subprocess,
        "run",
        lambda *a, **k: pytest.fail("nothing to install"),
    )

    assert cli_upgrade.run(check=False) == 0
    assert "nothing to do" in capsys.readouterr().out


def test_archive_install_prints_verifiable_steps_instead_of_overwriting(
    monkeypatch, capsys
):
    """hybro never replaces its own directory, so it hands over exact commands."""
    monkeypatch.setattr(cli_upgrade, "installation", lambda: cli_upgrade.ARCHIVE)
    monkeypatch.setattr(cli_upgrade, "version", lambda: "0.2.15")
    monkeypatch.setattr(cli_upgrade, "latest", lambda: "0.3.0")
    monkeypatch.setattr(cli_upgrade, "platform_name", lambda: "darwin-arm64")
    monkeypatch.setattr(
        cli_upgrade.subprocess, "run", lambda *a, **k: pytest.fail("must not install")
    )

    assert cli_upgrade.run(check=False) == 1
    output = capsys.readouterr().out
    assert "hybro-0.3.0-darwin-arm64.tar.gz" in output
    assert ".sha256" in output
    assert "shasum -a 256 --check" in output


def test_checkout_refuses_to_manage_its_own_update(monkeypatch):
    monkeypatch.setattr(cli_upgrade, "installation", lambda: cli_upgrade.CHECKOUT)
    with pytest.raises(RuntimeConfigurationError, match="source checkout"):
        cli_upgrade.run(check=False)


def test_npm_failure_is_reported_without_claiming_success(monkeypatch):
    monkeypatch.setattr(cli_upgrade, "installation", lambda: cli_upgrade.NPM)
    monkeypatch.setattr(cli_upgrade, "version", lambda: "0.2.15")
    monkeypatch.setattr(cli_upgrade, "latest", lambda: "0.3.0")
    monkeypatch.setattr(
        cli_upgrade.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=1),
    )

    with pytest.raises(RuntimeConfigurationError, match="could not install"):
        cli_upgrade.run(check=False)


def test_unrecognized_versions_are_refused_rather_than_compared(monkeypatch):
    monkeypatch.setattr(cli_upgrade, "installation", lambda: cli_upgrade.NPM)
    monkeypatch.setattr(cli_upgrade, "version", lambda: "0.2.15")
    monkeypatch.setattr(cli_upgrade, "latest", lambda: "not-a-version")

    with pytest.raises(RuntimeConfigurationError, match="Unrecognized version"):
        cli_upgrade.run(check=True)
