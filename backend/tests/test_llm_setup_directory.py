from __future__ import annotations

import io
import os
import stat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from llm_gateway.runtime_config import RuntimeConfigurationError
from llm_gateway.runtime_store import RuntimeConfigStore, prepare_setup_directory
from llm_gateway.setup_cli import SetupConsole, main
from llm_gateway.setup_service import ModelChoices


def contents(home: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in home.iterdir()}


def directory(tmp_path: Path, mode: int) -> Path:
    home = tmp_path / "runtime"
    home.mkdir(mode=0o700)
    (home / "config.json").write_bytes(b"fixture config bytes\n")
    (home / "auth.json").write_bytes(b"fixture auth bytes\n")
    home.chmod(mode)
    return home


@pytest.mark.parametrize("mode", [0o755, 0o775])
def test_setup_repairs_only_owned_directory_mode(tmp_path: Path, mode: int) -> None:
    home = directory(tmp_path, mode)
    before = contents(home)
    files = {path.name: path.stat() for path in home.iterdir()}
    assert prepare_setup_directory(home)
    assert stat.S_IMODE(home.stat().st_mode) == 0o700
    assert contents(home) == before
    for path in home.iterdir():
        old, new = files[path.name], path.stat()
        assert (old.st_ino, old.st_mode, old.st_mtime_ns) == (
            new.st_ino,
            new.st_mode,
            new.st_mtime_ns,
        )


def test_private_directory_is_noop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = directory(tmp_path, 0o700)
    before = contents(home)
    chmod = Mock(side_effect=AssertionError("must not chmod a private directory"))
    monkeypatch.setattr(os, "fchmod", chmod)
    assert not prepare_setup_directory(home)
    chmod.assert_not_called()
    assert contents(home) == before


def test_absent_directory_is_not_created(tmp_path: Path) -> None:
    home = tmp_path / "missing" / "runtime"
    assert not prepare_setup_directory(home)
    assert not home.parent.exists()


@pytest.mark.parametrize("dangling", [False, True])
def test_symlink_refused_and_target_untouched(tmp_path: Path, dangling: bool) -> None:
    target = directory(tmp_path, 0o775)
    before = contents(target)
    home = tmp_path / "link"
    home.symlink_to(
        tmp_path / "absent" if dangling else target, target_is_directory=True
    )
    with pytest.raises(RuntimeConfigurationError, match="symlink; set HYBRO_HOME"):
        prepare_setup_directory(home)
    assert stat.S_IMODE(target.stat().st_mode) == 0o775
    assert contents(target) == before
    assert not (tmp_path / "absent").exists()


def test_non_directory_refused(tmp_path: Path) -> None:
    home = tmp_path / "file"
    home.write_bytes(b"untouched")
    before = home.stat()
    with pytest.raises(
        RuntimeConfigurationError, match="not a directory; set HYBRO_HOME"
    ):
        prepare_setup_directory(home)
    assert home.read_bytes() == b"untouched"
    assert home.stat().st_mode == before.st_mode


def test_foreign_owner_refused_from_fd_even_when_private(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = directory(tmp_path, 0o700)
    before = contents(home)
    monkeypatch.setattr(
        os,
        "fstat",
        lambda fd: SimpleNamespace(
            st_mode=stat.S_IFDIR | 0o700,
            st_uid=os.geteuid() + 1,
        ),
    )
    chmod = Mock(side_effect=AssertionError("foreign directory must not be changed"))
    monkeypatch.setattr(os, "fchmod", chmod)
    with pytest.raises(
        RuntimeConfigurationError, match="owned by another user.*administrator"
    ):
        prepare_setup_directory(home)
    chmod.assert_not_called()
    assert contents(home) == before


@pytest.mark.parametrize("replace_before_open", [True, False])
def test_path_replacement_cannot_redirect_chmod_through_symlink(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, replace_before_open: bool
) -> None:
    home = directory(tmp_path, 0o775)
    moved, target = tmp_path / "moved", tmp_path / "target"
    target.mkdir(mode=0o755)
    target.chmod(0o755)
    original_open, original_fstat = os.open, os.fstat

    def replace() -> None:
        home.rename(moved)
        home.symlink_to(target, target_is_directory=True)

    def open_directory(path: Path, flags: int) -> int:
        assert path == home
        assert flags & os.O_NOFOLLOW and flags & os.O_DIRECTORY
        if replace_before_open:
            replace()
        return original_open(path, flags)

    def fstat(fd: int) -> os.stat_result:
        replace()
        return original_fstat(fd)

    monkeypatch.setattr(os, "open", open_directory)
    if replace_before_open:
        with pytest.raises(
            RuntimeConfigurationError, match="Cannot secure runtime directory"
        ):
            prepare_setup_directory(home)
        assert stat.S_IMODE(moved.stat().st_mode) == 0o775
    else:
        monkeypatch.setattr(os, "fstat", fstat)
        assert prepare_setup_directory(home)
        assert stat.S_IMODE(moved.stat().st_mode) == 0o700
    assert stat.S_IMODE(target.stat().st_mode) == 0o755
    assert list(target.iterdir()) == []
    assert contents(moved) == {
        "config.json": b"fixture config bytes\n",
        "auth.json": b"fixture auth bytes\n",
    }


def test_ancestor_replacement_cannot_redirect_permission_repair(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ancestor, target = tmp_path / "ancestor", tmp_path / "target"
    ancestor.mkdir()
    target.mkdir()
    home = directory(ancestor, 0o775)
    other = directory(target, 0o755)
    before, other_before = contents(home), contents(other)
    moved = tmp_path / "moved"
    original_open = os.open
    opened = []

    def replace_and_open(path: Path, flags: int) -> int:
        assert path == home
        ancestor.rename(moved)
        ancestor.symlink_to(target, target_is_directory=True)
        fd = original_open(path, flags)
        opened.append(fd)
        return fd

    monkeypatch.setattr(os, "open", replace_and_open)
    chmod = Mock(side_effect=AssertionError("replacement must not be changed"))
    monkeypatch.setattr(os, "fchmod", chmod)
    with pytest.raises(RuntimeConfigurationError, match="changed during setup"):
        prepare_setup_directory(home)
    chmod.assert_not_called()
    assert stat.S_IMODE((moved / "runtime").stat().st_mode) == 0o775
    assert stat.S_IMODE(other.stat().st_mode) == 0o755
    assert contents(moved / "runtime") == before
    assert contents(other) == other_before
    with pytest.raises(OSError):
        os.fstat(opened[0])


def test_repair_failure_closes_fd_and_preserves_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = directory(tmp_path, 0o775)
    before = contents(home)
    chmod = Mock(side_effect=OSError("fixture failure"))
    monkeypatch.setattr(os, "fchmod", chmod)
    with pytest.raises(
        RuntimeConfigurationError, match="Cannot secure runtime directory"
    ):
        prepare_setup_directory(home)
    fd, mode = chmod.call_args.args
    assert mode == 0o700
    with pytest.raises(OSError):
        os.fstat(fd)
    assert contents(home) == before
    assert stat.S_IMODE(home.stat().st_mode) == 0o775


@pytest.mark.parametrize("mode", [0o755, 0o775])
def test_runtime_still_rejects_insecure_directory_without_repair(
    tmp_path: Path, mode: int
) -> None:
    home = directory(tmp_path, mode)
    before = contents(home)
    with pytest.raises(RuntimeConfigurationError, match="mode 0700"):
        RuntimeConfigStore(home).read_stored_credential()
    assert stat.S_IMODE(home.stat().st_mode) == mode
    assert contents(home) == before


def test_preflight_repairs_and_reports_before_first_prompt_without_reading_contents(
    tmp_path: Path,
) -> None:
    home = directory(tmp_path, 0o775)
    before = contents(home)
    reports: list[str] = []

    def cancel(*args: object) -> str:
        assert stat.S_IMODE(home.stat().st_mode) == 0o700
        assert (
            reports[0]
            == "Corrected runtime directory permissions to 0700; contents unchanged."
        )
        raise KeyboardInterrupt

    verifier = AsyncMock()
    assert (
        main(
            [],
            environment={"HYBRO_HOME": str(home)},
            catalog=lambda provider: ModelChoices(("fixture",)),
            verifier=verifier,
            console=SetupConsole(cancel, Mock(), reports.append),
        )
        == 130
    )
    assert contents(home) == before
    verifier.assert_not_awaited()


@pytest.mark.parametrize("kind", ["symlink", "file", "foreign"])
def test_invalid_directory_stops_before_prompt_or_oauth_login(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str
) -> None:
    target = directory(tmp_path, 0o775)
    home = tmp_path / "invalid"
    expected = "symlink"
    if kind == "symlink":
        home.symlink_to(target, target_is_directory=True)
    elif kind == "file":
        home.write_bytes(b"fixture")
        expected = "not a directory"
    else:
        home = target
        expected = "owned by another user"
        monkeypatch.setattr(
            os,
            "fstat",
            lambda fd: SimpleNamespace(
                st_mode=stat.S_IFDIR | 0o775,
                st_uid=os.geteuid() + 1,
            ),
        )
    prompt, secret, verifier, login = Mock(), Mock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr("llm_gateway.openai_oauth.login", login)
    error = io.StringIO()
    assert (
        main(
            ["--provider", "openai", "--auth", "oauth"],
            environment={"HYBRO_HOME": str(home)},
            catalog=lambda provider: ModelChoices(("fixture",)),
            verifier=verifier,
            console=SetupConsole(prompt, secret, lambda message: None),
            error_output=error,
        )
        == 1
    )
    assert expected in error.getvalue()
    prompt.assert_not_called()
    secret.assert_not_called()
    login.assert_not_awaited()
    verifier.assert_not_awaited()
    assert stat.S_IMODE(target.stat().st_mode) == 0o775


@pytest.mark.parametrize(
    "argv",
    [
        ["--help"],
        ["--bad"],
        ["--non-interactive"],
        [
            "--non-interactive",
            "--provider",
            "openai",
            "--auth",
            "api_key",
            "--text-model",
            "invalid",
        ],
    ],
)
def test_invalid_selection_or_help_creates_no_tree(
    tmp_path: Path, argv: list[str]
) -> None:
    home = tmp_path / "absent" / "runtime"
    verifier = AsyncMock()
    kwargs = dict(
        environment={"HYBRO_HOME": str(home)},
        catalog=lambda provider: ModelChoices(("fixture",)),
        verifier=verifier,
    )
    if argv == ["--help"]:
        with pytest.raises(SystemExit) as result:
            main(argv, **kwargs)
        assert result.value.code == 0
    else:
        assert main(argv, **kwargs) == 1
    assert not home.parent.exists()
    verifier.assert_not_awaited()
