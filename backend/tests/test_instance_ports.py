"""Dev and release instance ports; fake subprocesses and synthetic config only."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import cli_ports
import cli_stack
import configuration_cli as cli
from common.config.runtime_config import RuntimeConfig, RuntimeConfigurationError
from common.config.runtime_store import RuntimeConfigStore, parse_config
from tests.test_json_runtime_config import configured, selection

AGENTS = {"weather-agent": 7001, "image-generator-agent": 6002}


def document(**sections):
    data = selection().model_dump(exclude_none=True)
    data.update(sections)
    return data


def config_with(**sections) -> RuntimeConfig:
    return parse_config(json.dumps(document(**sections)).encode())


# ---- Schema: structure only, valid in every layer ----


def test_config_accepts_instances_and_keeps_files_without_them_unchanged():
    config = config_with(
        instances={
            "dev": {"ports": {"frontend": 3200, "agents": {"weather-agent": 7201}}}
        }
    )
    assert config.instances.dev.ports.frontend == 3200
    assert config.instances.dev.ports.agents == {"weather-agent": 7201}
    assert config.instances.release is None
    assert "instances" not in selection().model_dump(exclude_none=True)


@pytest.mark.parametrize(
    "instances,field",
    [
        ({"staging": {}}, "instances"),
        ({"dev": {"ports": {"frontend": 80}}}, "instances.dev.ports.frontend"),
        ({"dev": {"ports": {"frontend": 70000}}}, "instances.dev.ports.frontend"),
        ({"dev": {"ports": {"frontend": True}}}, "instances.dev.ports.frontend"),
        ({"dev": {"ports": {"frontend": "3100"}}}, "instances.dev.ports.frontend"),
        ({"dev": {"ports": {"web": 3100}}}, "instances.dev.ports"),
        ({"dev": {"hosts": {}}}, "instances.dev"),
        ({"dev": {"ports": {"agents": {"Bad Name": 7201}}}}, "instances.dev.ports"),
    ],
)
def test_config_rejects_invalid_instances_with_named_field(instances, field):
    with pytest.raises(RuntimeConfigurationError) as caught:
        config_with(instances=instances)
    assert field in str(caught.value)


def test_config_rejects_a_stored_mcp_url():
    with pytest.raises(RuntimeConfigurationError) as caught:
        config_with(frontend={"mcp_url": "http://127.0.0.1:8001/mcp"})
    assert "frontend: derived_from_instance_ports" in str(caught.value)


# ---- Effective ports ----


def test_defaults_per_instance_follow_the_manifest():
    release = cli_ports.effective_ports(None, "release", AGENTS)
    dev = cli_ports.effective_ports(None, "dev", AGENTS)
    assert release.services == {
        "frontend": 3000,
        "backend": 8000,
        "mcp": 8001,
        "mongo": 27017,
        "redis": 6379,
    }
    assert release.agents == AGENTS
    assert dev.services == {
        "frontend": 3100,
        "backend": 8100,
        "mcp": 8101,
        "mongo": 27117,
        "redis": 6479,
    }
    assert dev.agents == {"weather-agent": 7101, "image-generator-agent": 6102}
    assert dev.from_defaults == frozenset(
        {"frontend", "backend", "mcp", "mongo", "redis"}
        | {f"agents.{name}" for name in AGENTS}
    )


def test_overrides_replace_only_their_own_ports():
    config = config_with(
        instances={
            "dev": {"ports": {"frontend": 3200, "agents": {"weather-agent": 7201}}}
        }
    )
    dev = cli_ports.effective_ports(config, "dev", AGENTS)
    assert dev.services["frontend"] == 3200
    assert dev.services["backend"] == 8100
    assert dev.agents == {"weather-agent": 7201, "image-generator-agent": 6102}
    assert "frontend" not in dev.from_defaults
    assert "agents.weather-agent" not in dev.from_defaults
    assert (
        cli_ports.effective_ports(config, "release", AGENTS).services["frontend"]
        == 3000
    )


def test_validate_accepts_defaults():
    running, other = cli_ports.validate_ports(config_with(), "dev", AGENTS)
    assert running.instance == "dev" and other.instance == "release"


def test_duplicate_within_an_instance_names_both_fields():
    config = config_with(instances={"dev": {"ports": {"backend": 3100}}})
    with pytest.raises(RuntimeConfigurationError) as caught:
        cli_ports.validate_ports(config, "dev", AGENTS)
    message = str(caught.value)
    assert "instances.dev.ports.backend" in message
    assert "instances.dev.ports.frontend" in message
    assert "3100" in message


def test_overlap_across_instances_is_rejected_from_either_side():
    config = config_with(instances={"dev": {"ports": {"frontend": 3000}}})
    for instance in ("dev", "release"):
        with pytest.raises(RuntimeConfigurationError) as caught:
            cli_ports.validate_ports(config, instance, AGENTS)
        message = str(caught.value)
        assert "instances.dev.ports.frontend" in message
        assert "instances.release.ports.frontend" in message


def test_running_instance_agent_keys_must_be_in_the_manifest():
    config = config_with(
        instances={"dev": {"ports": {"agents": {"missing-agent": 7301}}}}
    )
    with pytest.raises(RuntimeConfigurationError) as caught:
        cli_ports.validate_ports(config, "dev", AGENTS)
    assert "instances.dev.ports.agents.missing-agent" in str(caught.value)


def test_other_instance_agent_keys_are_used_as_given():
    """A release CLI's bundled manifest may lack an agent that dev added."""
    config = config_with(instances={"dev": {"ports": {"agents": {"new-agent": 7301}}}})
    _, dev = cli_ports.validate_ports(config, "release", AGENTS)
    assert dev.agents["new-agent"] == 7301

    clash = config_with(instances={"dev": {"ports": {"agents": {"new-agent": 7001}}}})
    with pytest.raises(RuntimeConfigurationError) as caught:
        cli_ports.validate_ports(clash, "release", AGENTS)
    assert "instances.dev.ports.agents.new-agent" in str(caught.value)


def test_manifest_agents_reads_enabled_services():
    agents = cli_ports.manifest_agents()
    assert agents["weather-agent"] == 7001
    assert all(isinstance(port, int) for port in agents.values())


# ---- Store integration ----


def test_update_config_round_trips_instance_ports(tmp_path):
    runtime = configured(tmp_path)
    runtime.update_config(["instances", "dev", "ports", "frontend"], 3200)
    assert runtime.read_config().instances.dev.ports.frontend == 3200


def test_update_config_validate_rejects_before_writing(tmp_path):
    runtime = configured(tmp_path)
    before = (runtime.home / "config.json").read_bytes()

    def validate(config: RuntimeConfig) -> None:
        cli_ports.validate_ports(config, "dev", AGENTS)

    with pytest.raises(RuntimeConfigurationError):
        runtime.update_config(
            ["instances", "dev", "ports", "frontend"], 3000, validate=validate
        )
    with pytest.raises(RuntimeConfigurationError):
        runtime.update_config(["instances", "dev", "ports", "frontend"], 80)
    assert (runtime.home / "config.json").read_bytes() == before


def test_setup_save_keeps_instance_ports(tmp_path):
    from pydantic import SecretStr

    from common.config.runtime_config import ApiKeyCredential

    runtime = configured(tmp_path)
    runtime.update_config(["instances", "dev", "ports", "frontend"], 3200)
    runtime.save(
        selection().model_copy(
            update={"models": selection().models.model_copy(update={"text": "gpt-4o"})}
        ),
        ApiKeyCredential(provider="openai", api_key=SecretStr("fixture-key")),
        {},
    )
    config = runtime.read_config()
    assert config.models.text == "gpt-4o"
    assert config.instances.dev.ports.frontend == 3200


async def test_oauth_refresh_ignores_an_instance_port_change(tmp_path):
    from tests.fakes.llm_runtime import oauth_config, oauth_credential

    runtime = RuntimeConfigStore(tmp_path / "runtime")
    credential = oauth_credential()
    runtime.save(oauth_config(), credential, {})
    expected = runtime.read_config()
    runtime.update_config(["instances", "release", "ports", "frontend"], 3300)

    async def refresh(current):
        raise AssertionError("a fresh token needs no refresh")

    resolved = await runtime.resolve_oauth(expected, credential.account_id, {}, refresh)
    assert resolved.account_id == credential.account_id


# ---- Read-only lookup for status and display ----


def test_instance_ports_without_config_uses_defaults(tmp_path):
    ports, notice = cli_ports.instance_ports(
        "dev", RuntimeConfigStore(tmp_path / "absent"), AGENTS
    )
    assert ports.services["mcp"] == 8101
    assert notice is None
    assert not (tmp_path / "absent").exists()


def test_instance_ports_applies_overrides(tmp_path):
    runtime = configured(tmp_path)
    runtime.update_config(["instances", "dev", "ports", "mcp"], 8201)
    ports, notice = cli_ports.instance_ports("dev", runtime, AGENTS)
    assert ports.services["mcp"] == 8201 and notice is None


def test_instance_ports_with_invalid_config_falls_back_with_notice(tmp_path):
    runtime = configured(tmp_path)
    data = json.loads((runtime.home / "config.json").read_text())
    data["instances"] = {"dev": {"ports": {"mcp": 1}}}
    (runtime.home / "config.json").write_text(json.dumps(data))
    ports, notice = cli_ports.instance_ports("dev", runtime, AGENTS)
    assert ports.services["mcp"] == 8101
    assert notice and "config.json" in notice


# ---- Derived public values ----


def test_derived_public_values_follow_the_instance():
    dev = cli_ports.effective_ports(None, "dev", AGENTS)
    public, warnings = cli_ports.derive_public({}, dev)
    assert public == {
        "api_base_url": "http://localhost:8100",
        "server_url": "http://localhost:3100",
        "mcp_url": "http://127.0.0.1:8101/mcp",
    }
    assert warnings == []
    assert cli_ports.frontend_origin(dev) == "http://localhost:3100"


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1"])
def test_loopback_public_urls_are_ignored_with_a_warning(host):
    dev = cli_ports.effective_ports(None, "dev", AGENTS)
    public, warnings = cli_ports.derive_public(
        {"api_base_url": f"http://{host}:8000", "server_url": f"http://{host}:3000"},
        dev,
    )
    assert public["api_base_url"] == "http://localhost:8100"
    assert public["server_url"] == "http://localhost:3100"
    assert len(warnings) == 2
    assert all("delete it" in warning for warning in warnings)
    assert any("frontend.api_base_url" in warning for warning in warnings)


def test_public_urls_on_other_hosts_are_kept_with_a_warning():
    dev = cli_ports.effective_ports(None, "dev", AGENTS)
    public, warnings = cli_ports.derive_public(
        {"api_base_url": "https://api.example.org"}, dev
    )
    assert public["api_base_url"] == "https://api.example.org"
    assert public["server_url"] == "http://localhost:3100"
    assert len(warnings) == 1 and "both instances" in warnings[0]


def test_transport_environment_names_every_port_and_both_exclusions():
    release = cli_ports.effective_ports(None, "release", AGENTS)
    dev = cli_ports.effective_ports(None, "dev", AGENTS)
    env = cli_ports.transport_environment(dev, release)
    assert env["HYBRO_PORT_FRONTEND"] == "3100"
    assert env["HYBRO_PORT_BACKEND"] == "8100"
    assert env["HYBRO_PORT_MCP"] == "8101"
    assert env["HYBRO_PORT_MONGO"] == "27117"
    assert env["HYBRO_PORT_REDIS"] == "6479"
    assert env["HYBRO_PORT_WEATHER_AGENT"] == "7101"
    assert env["HYBRO_PORT_IMAGE_GENERATOR_AGENT"] == "6102"
    assert env["HYBRO_FRONTEND_ORIGIN"] == "http://localhost:3100"
    assert set(env["HYBRO_DISCOVERY_EXCLUDED_PORTS"].split(",")) == {
        "7001",
        "6002",
        "7101",
        "6102",
    }


# ---- Stack identity ----


def test_checkout_is_dev_and_install_is_release(monkeypatch):
    assert cli_stack.resolve().instance == "dev"
    repo = Path(cli_stack.__file__).resolve().parents[1]
    monkeypatch.setattr(cli_stack, "_frozen_root", lambda: repo)
    monkeypatch.setattr(cli_stack, "checkout_root", lambda: None)
    assert cli_stack.resolve().instance == "release"


# ---- CLI projection ----


@pytest.fixture
def compose_calls(monkeypatch):
    calls = []

    def run(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(cli.subprocess, "run", run)
    return calls


def test_start_exports_dev_ports_and_derived_public_config(
    monkeypatch, tmp_path, compose_calls, capsys
):
    runtime = configured(tmp_path)
    runtime.update_config(["frontend", "api_base_url"], "http://localhost:8000")
    monkeypatch.setenv("HYBRO_HOME", str(runtime.home))
    monkeypatch.setenv("HYBRO_PORT_FRONTEND", "1")
    monkeypatch.setenv("HYBRO_FRONTEND_ORIGIN", "http://evil.example")
    monkeypatch.setenv("HYBRO_DISCOVERY_EXCLUDED_PORTS", "1")
    assert cli.main(["start"]) == 0
    env = compose_calls[-1][1]["env"]
    assert env["HYBRO_PORT_FRONTEND"] == "3100"
    assert env["HYBRO_FRONTEND_ORIGIN"] == "http://localhost:3100"
    assert "7101" in env["HYBRO_DISCOVERY_EXCLUDED_PORTS"].split(",")
    public = json.loads(env["HYBRO_FRONTEND_CONFIG"])
    assert public["api_base_url"] == "http://localhost:8100"
    assert public["server_url"] == "http://localhost:3100"
    assert public["mcp_url"] == "http://127.0.0.1:8101/mcp"
    assert "frontend.api_base_url" in capsys.readouterr().err


def test_start_rejects_overlapping_ports_without_running_docker(
    monkeypatch, tmp_path, compose_calls, capsys
):
    runtime = configured(tmp_path)
    data = json.loads((runtime.home / "config.json").read_text())
    data["instances"] = {"dev": {"ports": {"frontend": 3000}}}
    (runtime.home / "config.json").write_text(json.dumps(data))
    monkeypatch.setenv("HYBRO_HOME", str(runtime.home))
    assert cli.main(["start"]) == 1
    assert compose_calls == []
    assert "instances.dev.ports.frontend" in capsys.readouterr().err


def test_status_commands_read_no_ports_and_strip_inherited_ones(
    monkeypatch, tmp_path, compose_calls
):
    home = tmp_path / "absent"
    monkeypatch.setenv("HYBRO_HOME", str(home))
    monkeypatch.setenv("HYBRO_PORT_FRONTEND", "1")
    monkeypatch.setenv("HYBRO_DISCOVERY_EXCLUDED_PORTS", "1")
    assert cli.main(["stop"]) == 0
    env = compose_calls[-1][1]["env"]
    assert not any(key.startswith("HYBRO_PORT_") for key in env)
    assert "HYBRO_DISCOVERY_EXCLUDED_PORTS" not in env
    assert "HYBRO_FRONTEND_ORIGIN" not in env
    assert not home.exists()


def test_failed_start_names_the_ports_command(monkeypatch, tmp_path, capsys):
    runtime = configured(tmp_path)
    monkeypatch.setenv("HYBRO_HOME", str(runtime.home))
    monkeypatch.setattr(
        cli.subprocess, "run", lambda arguments, **kwargs: SimpleNamespace(returncode=1)
    )
    assert cli.main(["start"]) == 1
    err = capsys.readouterr().err
    assert "hybro config set instances.dev.ports." in err
    assert "3100" in err


def test_config_set_instance_port_validates_effective_ports(
    monkeypatch, tmp_path, capsys
):
    runtime = configured(tmp_path)
    monkeypatch.setenv("HYBRO_HOME", str(runtime.home))
    assert cli.main(["config", "set", "instances.dev.ports.frontend", "3200"]) == 0
    assert runtime.read_config().instances.dev.ports.frontend == 3200
    out = capsys.readouterr().out
    assert "older" in out and "release" in out
    before = (runtime.home / "config.json").read_bytes()
    assert cli.main(["config", "set", "instances.dev.ports.backend", "3000"]) == 1
    assert "instances.release.ports.frontend" in capsys.readouterr().err
    assert (runtime.home / "config.json").read_bytes() == before


def test_config_check_reports_derived_url_warnings(monkeypatch, tmp_path, capsys):
    runtime = configured(tmp_path)
    runtime.update_config(["frontend", "server_url"], "http://localhost:3000")
    monkeypatch.setenv("HYBRO_HOME", str(runtime.home))
    assert cli.main(["config", "check"]) == 0
    assert "frontend.server_url" in capsys.readouterr().err


def test_mcp_status_uses_the_instance_port_even_with_invalid_config(
    monkeypatch, tmp_path, capsys
):
    runtime = configured(tmp_path)
    data = json.loads((runtime.home / "config.json").read_text())
    data["instances"] = {"dev": {"ports": {"mcp": 1}}}
    (runtime.home / "config.json").write_text(json.dumps(data))
    monkeypatch.setenv("HYBRO_HOME", str(runtime.home))
    probed = []
    monkeypatch.setattr(
        cli, "read_mcp_status", lambda port: probed.append(port) or "ready"
    )
    assert cli.main(["mcp", "status"]) == 0
    assert probed == [8101]
    captured = capsys.readouterr()
    assert "http://127.0.0.1:8101/mcp" in captured.out
    assert "showing default ports" in captured.err


# ---- Backend container origin merge ----


@pytest.fixture
def container_settings(monkeypatch, tmp_path):
    from common.config.loader import get_settings

    runtime = configured(tmp_path)
    monkeypatch.setenv("HYBRO_HOME", str(runtime.home))
    monkeypatch.setenv("HYBRO_CONTAINER", "1")
    get_settings.cache_clear()
    yield runtime, get_settings
    get_settings.cache_clear()


def test_container_origin_replaces_loopback_defaults(monkeypatch, container_settings):
    _, get_settings = container_settings
    monkeypatch.setenv("HYBRO_FRONTEND_ORIGIN", "http://localhost:3100")
    assert get_settings().frontend_origins == [
        "http://localhost:3100",
        "https://hybro.ai",
    ]


def test_container_origin_keeps_configured_public_entries(
    monkeypatch, container_settings
):
    runtime, get_settings = container_settings
    runtime.update_config(
        ["backend", "frontend_origins"],
        ["http://127.0.0.1:3000", "https://app.example.org"],
    )
    monkeypatch.setenv("HYBRO_FRONTEND_ORIGIN", "http://localhost:3100")
    assert get_settings().frontend_origins == [
        "http://localhost:3100",
        "https://app.example.org",
    ]


def test_without_container_origin_settings_are_unchanged(
    monkeypatch, container_settings
):
    _, get_settings = container_settings
    monkeypatch.delenv("HYBRO_FRONTEND_ORIGIN", raising=False)
    assert get_settings().frontend_origins == [
        "http://localhost:3000",
        "https://hybro.ai",
    ]


def test_config_set_repairs_an_invalid_hand_edited_port(monkeypatch, tmp_path, capsys):
    runtime = configured(tmp_path)
    data = json.loads((runtime.home / "config.json").read_text())
    data["instances"] = {"dev": {"ports": {"mcp": 1}}}
    (runtime.home / "config.json").write_text(json.dumps(data))
    monkeypatch.setenv("HYBRO_HOME", str(runtime.home))
    assert cli.main(["config", "set", "instances.dev.ports.mcp", "8201"]) == 0
    assert runtime.read_config().instances.dev.ports.mcp == 8201
    # The file already had instances, so this is not a first write.
    assert "older" not in capsys.readouterr().out


def test_set_port_repairs_an_invalid_port_and_warns_only_on_first_write(
    monkeypatch, tmp_path
):
    runtime = configured(tmp_path)
    monkeypatch.setenv("HYBRO_HOME", str(runtime.home))
    first = cli.set_port("frontend", 3200)
    assert "instances.dev.ports.frontend = 3200" in first and "older" in first
    data = json.loads((runtime.home / "config.json").read_text())
    data["instances"]["dev"]["ports"]["mcp"] = 1
    (runtime.home / "config.json").write_text(json.dumps(data))
    again = cli.set_port("mcp", 8201)
    assert "older" not in again
    assert runtime.read_config().instances.dev.ports.mcp == 8201
    with pytest.raises(RuntimeConfigurationError):
        cli.set_port("backend", 3000)


def test_migration_skips_derived_public_fields(monkeypatch, tmp_path):
    runtime = configured(tmp_path)
    (runtime.home / "config.json").unlink()
    (runtime.home / "config.yaml").write_text(
        "version: 1\nprovider: {id: openai, auth: api_key}\nmodels: {text: gpt-4o-mini}\n"
    )
    env = tmp_path / "legacy.env"
    env.write_text("NEXT_PUBLIC_MCP_URL=http://127.0.0.1:8001/mcp\n")
    monkeypatch.setenv("HYBRO_HOME", str(runtime.home))
    assert cli.main(["config", "migrate", "--from-env", str(env)]) == 0
    assert "mcp_url" not in runtime.read_config().frontend
