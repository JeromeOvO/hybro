from __future__ import annotations

from pathlib import Path

from common.config.settings import resolve_settings_env_file


def test_resolve_settings_env_file_prefers_root_when_present(tmp_path: Path) -> None:
    repo = tmp_path
    backend = repo / "backend"
    backend.mkdir()
    (repo / "docker-compose.yml").write_text("services: {}\n")
    (repo / ".env").write_text("OPENAI_API_KEY=root\n")
    (backend / ".env").write_text("OPENAI_API_KEY=backend\n")

    assert resolve_settings_env_file(str(backend)) == str(repo / ".env")


def test_resolve_settings_env_file_falls_back_to_backend(tmp_path: Path) -> None:
    repo = tmp_path
    backend = repo / "backend"
    backend.mkdir()
    (repo / "docker-compose.yml").write_text("services: {}\n")
    (backend / ".env").write_text("OPENAI_API_KEY=backend\n")

    assert resolve_settings_env_file(str(backend)) == str(backend / ".env")


def test_resolve_settings_env_file_without_compose_uses_backend(tmp_path: Path) -> None:
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / ".env").write_text("OPENAI_API_KEY=backend\n")

    assert resolve_settings_env_file(str(backend)) == str(backend / ".env")
