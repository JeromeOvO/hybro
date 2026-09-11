# PyInstaller spec for the standalone hybro CLI.
#
# Build through packaging/cli/build.sh, which supplies this list's dependencies.
# `_frozen_root()` in backend/cli_stack.py reads sys._MEIPASS, so the four data
# files below must land at these relative paths and the bundle must be a
# directory (PyInstaller onedir): a self-deleting onefile bundle would move the
# Compose file between runs and make Compose recreate every container.
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
    [],
    exclude_binaries=True,
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

collect = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="hybro",
)
