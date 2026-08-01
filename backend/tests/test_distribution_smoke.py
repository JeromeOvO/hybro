from __future__ import annotations

import os
import shutil
import site
import subprocess
import sys
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_RUNTIME_MODULES = (
    "common.prompts",
    "execution.repository",
)
REQUIRED_WHEEL_PATHS = tuple(
    f"{module.replace('.', '/')}/__init__.py" for module in REQUIRED_RUNTIME_MODULES
)


def _run(*args: str, cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


def test_built_wheel_installs_runtime_subpackages(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    assert uv is not None, "uv is required to run the distribution smoke test"

    source_root = tmp_path / "source"
    shutil.copytree(
        PROJECT_ROOT,
        source_root,
        ignore=shutil.ignore_patterns(
            ".env",
            ".git",
            ".pi-subagents",
            ".pytest_cache",
            ".ruff_cache",
            ".venv",
            "__pycache__",
            "build",
            "dist",
            "*.egg-info",
        ),
    )

    wheel_dir = tmp_path / "wheel"
    _run(uv, "build", "--wheel", "--out-dir", str(wheel_dir), cwd=source_root)
    wheels = list(wheel_dir.glob("*.whl"))
    assert len(wheels) == 1
    wheel = wheels[0]

    with zipfile.ZipFile(wheel) as archive:
        wheel_paths = set(archive.namelist())
    assert set(REQUIRED_WHEEL_PATHS).issubset(wheel_paths)

    venv_dir = tmp_path / "venv"
    _run(
        uv,
        "venv",
        "--python",
        sys.executable,
        "--system-site-packages",
        str(venv_dir),
        cwd=tmp_path,
    )
    installed_python = venv_dir / (
        "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    )
    _run(
        uv,
        "pip",
        "install",
        "--python",
        str(installed_python),
        "--no-deps",
        str(wheel),
        cwd=tmp_path,
    )

    outside_project = tmp_path / "outside-project"
    outside_project.mkdir()
    smoke_code = """
import common.prompts
import execution.repository
from pathlib import Path
import sys

prefix = Path(sys.prefix).resolve()
for module in (common.prompts, execution.repository):
    assert prefix in Path(module.__file__).resolve().parents
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(site.getsitepackages())
    subprocess.run(
        (str(installed_python), "-c", smoke_code),
        cwd=outside_project,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
