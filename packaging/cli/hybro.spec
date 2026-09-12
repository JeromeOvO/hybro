# PyInstaller spec for the standalone hybro CLI.
#
# Build through packaging/cli/build.sh, which supplies this list's dependencies.
#
# Onefile, not onedir. npm's packer drops symbolic links entirely (verified with
# a minimal package), and a macOS PyInstaller onedir bundle depends on four of
# them -- `_internal/Python` plus three inside `Python.framework` -- so the npm
# channel shipped a CLI that could not start its own interpreter. Onefile emits
# a single executable with no links and unpacks itself at run time.
#
# Onefile normally risks Compose treating each run as a changed project, because
# it extracts to a fresh temporary directory. That does not apply here: both
# Compose files pin `name: hybro`, so the project identity is fixed regardless of
# where the stack file lands. `_frozen_root()` in backend/cli_stack.py reads
# sys._MEIPASS, which is that extraction directory.
#
# The static analysis follows the lazy imports inside functions, including
# `llm_gateway.cli_tui`, `llm_gateway.setup_cli`, and the providers they reach.
from pathlib import Path

REPO = Path(SPECPATH).resolve().parents[1]
BACKEND = REPO / "backend"

# (source, destination inside the bundle). The released Compose file travels with
# the CLI so an installed CLI needs no checkout; agents.yaml rides along because
# `render_compose.py` documents it as the agent manifest's single source.
datas = [
    (str(REPO / "VERSION"), "."),
    (str(REPO / "docker-compose.release.yml"), "."),
    (str(REPO / "scripts" / "hybro-help.txt"), "scripts"),
    (str(REPO / "default_agents" / "agents.yaml"), "default_agents"),
]

analysis = Analysis(
    [str(BACKEND / "configuration_cli.py")],
    pathex=[str(BACKEND)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    # The server stack is never imported by the CLI; excluding it keeps the
    # bundle to CLI-sized dependencies even if the build environment has them.
    excludes=[
        "fastapi",
        "motor",
        "pymongo",
        "redis",
        "tiktoken",
        "PIL",
        "aiohttp",
        "gunicorn",
        "clerk_backend_api",
        "jwcrypto",
        "pypdf",
        "pytest",
    ],
    noarchive=False,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="hybro",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
