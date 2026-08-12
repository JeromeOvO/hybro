"""Load the monorepo-root .env for host runs of default agents.

Under Docker Compose the process environment is already injected. Walking for
``docker-compose.yml`` then fails inside the image, and we fall back to the
default ``load_dotenv()`` search (no-op when no file is present).
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv


def load_repo_env(*, start: Path | None = None) -> None:
    """Load repo-root ``.env`` when found by walking parents for docker-compose.yml."""
    origin = (start or Path.cwd()).resolve()
    for parent in [origin, *origin.parents]:
        if (parent / "docker-compose.yml").is_file():
            env_path = parent / ".env"
            if env_path.is_file():
                load_dotenv(env_path, override=False)
            return
    load_dotenv()
