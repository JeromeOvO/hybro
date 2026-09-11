"""CLI checks use fake subprocesses and synthetic JSON credentials only."""

import json
from types import SimpleNamespace

import pytest

import configuration_cli as cli
from common.config.runtime_store import RuntimeConfigStore
from tests.test_json_runtime_config import configured


def test_start_without_setup_does_not_invoke_docker(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HYBRO_HOME", str(tmp_path / "absent"))
    monkeypatch.setattr(
        cli.subprocess, "run", lambda *a, **k: pytest.fail("unexpected process")
    )
    assert cli.main(["start"]) == 1
    assert "hybro setup" in capsys.readouterr().err


def test_start_projects_only_json_configuration(monkeypatch, tmp_path):
    runtime = configured(tmp_path)
    runtime.update_config(["frontend", "max_message_length"], 5432)
    monkeypatch.setenv("HYBRO_HOME", str(runtime.home))
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-secret")
    monkeypatch.setenv("NEXT_PUBLIC_PRIVATE_VALUE", "ambient-secret")
    monkeypatch.setenv("HYBRO_AGENT_CONFIG", "ambient-secret")
    calls = []

    def run(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(cli.subprocess, "run", run)
    assert cli.main(["start", "--build", "--recreate"]) == 0
    arguments, options = calls[-1]
    assert arguments[:4] == ["docker", "compose", "--env-file", "/dev/null"]
    assert "--build" in arguments and "--force-recreate" in arguments
    env = options["env"]
    assert "ambient-secret" not in json.dumps(env)
    public = json.loads(env["HYBRO_FRONTEND_CONFIG"])
    assert public["max_message_length"] == 5432
    assert "fixture-key" not in env["HYBRO_FRONTEND_CONFIG"]
    agent = json.loads(env["HYBRO_AGENT_CONFIG"])
    assert set(agent) == {"base_url", "token", "image_size"}
    assert (
        agent["token"] == runtime.read_service_credentials()["default_agent_llm_token"]
    )
    assert "fixture-key" not in json.dumps(agent)
    assert not (tmp_path / ".env").exists()


def test_extra_compose_files_must_exist_inside_the_repository():
    base = str(cli.ROOT / "docker-compose.yml")
    assert cli.compose_files() == [base]
    overlay = cli.ROOT / "docker-compose.ci.yml"
    assert overlay.is_file()
    assert cli.compose_files(["docker-compose.ci.yml"]) == [base, str(overlay)]
    for rejected in ("/etc/passwd", "../outside.yml", "docker-compose.missing.yml"):
        with pytest.raises(cli.RuntimeConfigurationError):
            cli.compose_files([rejected])


def test_start_forwards_explicit_compose_overlay(monkeypatch, tmp_path):
    runtime = configured(tmp_path)
    monkeypatch.setenv("HYBRO_HOME", str(runtime.home))
    calls = []

    def run(arguments, **kwargs):
        calls.append(arguments)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(cli.subprocess, "run", run)
    assert (
        cli.main(["start", "--build", "--compose-file", "docker-compose.ci.yml"]) == 0
    )
    # Renderer first, then Compose with the base file and the explicit overlay.
    assert calls[-1][:8] == [
        "docker",
        "compose",
        "--env-file",
        "/dev/null",
        "-f",
        str(cli.ROOT / "docker-compose.yml"),
        "-f",
        str(cli.ROOT / "docker-compose.ci.yml"),
    ]
    assert calls[-1][-4:] == ["up", "-d", "--remove-orphans", "--build"]


def test_config_set_and_show_do_not_expose_credentials(monkeypatch, tmp_path, capsys):
    runtime = configured(tmp_path)
    monkeypatch.setenv("HYBRO_HOME", str(runtime.home))
    assert cli.main(["config", "set", "backend.log_level", '"DEBUG"']) == 0
    assert runtime.read_config().backend["log_level"] == "DEBUG"
    assert cli.main(["config", "show"]) == 0
    assert "fixture-key" not in capsys.readouterr().out
    assert (
        cli.main(["config", "set", "backend.openai_api_key", '"private-marker"']) == 1
    )
    assert "private-marker" not in capsys.readouterr().err


def test_explicit_migration_preserves_auth_and_leaves_originals(monkeypatch, tmp_path):
    runtime = configured(tmp_path)
    (runtime.home / "config.json").unlink()
    legacy = runtime.home / "config.yaml"
    legacy.write_text(
        "version: 1\nprovider: {id: openai, auth: api_key}\nmodels: {text: gpt-4o-mini}\n"
    )
    env = tmp_path / "legacy.env"
    env.write_text(
        "AUTH_MODE=clerk\nCLERK_SECRET_KEY=fixture-clerk\nNEXT_PUBLIC_MAX_MESSAGE_LENGTH=3456\nMONGODB_URL=mongodb://127.0.0.1:27017/?replicaSet=rs0\n"
    )
    monkeypatch.setenv("HYBRO_HOME", str(runtime.home))
    assert cli.main(["config", "migrate", "--from-env", str(env)]) == 0
    assert legacy.is_file() and env.is_file()
    assert runtime.read_config().backend == {"auth_mode": "clerk"}
    assert runtime.read_config().frontend["max_message_length"] == 3456
    assert runtime.read_service_credentials()["clerk_secret_key"] == "fixture-clerk"
    assert (
        runtime.load({}).authentication.credential.api_key.get_secret_value()
        == "fixture-key"
    )
    assert cli.main(["config", "migrate"]) == 1


def test_invalid_migration_does_not_change_auth(monkeypatch, tmp_path):
    runtime = configured(tmp_path)
    (runtime.home / "config.json").unlink()
    (runtime.home / "config.yaml").write_text(
        "provider: {id: invalid, auth: api_key}\n"
    )
    auth = (runtime.home / "auth.json").read_bytes()
    monkeypatch.setenv("HYBRO_HOME", str(runtime.home))
    assert cli.main(["config", "migrate"]) == 1
    assert (runtime.home / "auth.json").read_bytes() == auth
    assert not (runtime.home / "config.json").exists()


@pytest.mark.parametrize("kind", ["missing", "symlink", "oversized"])
def test_migration_rejects_unsafe_explicit_environment_input(
    monkeypatch, tmp_path, kind
):
    runtime = configured(tmp_path)
    (runtime.home / "config.json").unlink()
    (runtime.home / "config.yaml").write_text(
        "provider: {id: openai, auth: api_key}\nmodels: {text: gpt-4o-mini}\n"
    )
    path = tmp_path / "legacy.env"
    if kind == "symlink":
        path.symlink_to(runtime.home / "auth.json")
    elif kind == "oversized":
        path.write_text("X" * (64 * 1024 + 1))
    before = (runtime.home / "auth.json").read_bytes()
    monkeypatch.setenv("HYBRO_HOME", str(runtime.home))
    assert cli.main(["config", "migrate", "--from-env", str(path)]) == 1
    assert (runtime.home / "auth.json").read_bytes() == before
    assert not (runtime.home / "config.json").exists()


def test_tui_entry_point_injects_the_host_service_status_reader(monkeypatch):
    """The gateway TUI never imports the host CLI; the entry point supplies status."""
    from llm_gateway import cli_tui

    captured = {}
    monkeypatch.setattr(cli_tui, "main", lambda **kwargs: captured.update(kwargs) or 0)
    assert cli.main([]) == 0
    assert captured == {"status": cli.service_status}


def test_status_does_not_create_runtime_or_read_dotenv(monkeypatch, tmp_path):
    runtime = RuntimeConfigStore(tmp_path / "absent")
    monkeypatch.setenv("HYBRO_HOME", str(runtime.home))
    calls = []
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda args, **kw: calls.append(args) or SimpleNamespace(returncode=0),
    )
    assert cli.main(["status"]) == 0
    assert calls[0][-2:] == ["ps", "--all"]
    assert not runtime.home.exists()
