"""Host ports of the dev and release instances, and the values derived from them.

Both instances read one ``config.json``. Overrides live under
``instances.<release|dev>.ports``; anything missing uses the instance default.
Only the host side of each published port is configurable: services reach each
other on the Compose network at their fixed container ports.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

import yaml

from cli_mcp import mcp_url as _mcp_address
from cli_stack import MANIFEST, Instance, data_file
from common.config.loader import LOOPBACK_HOSTS
from common.config.runtime_config import (
    InstancePorts,
    RuntimeConfig,
    RuntimeConfigurationError,
)
from common.config.runtime_store import RuntimeConfigStore

SERVICES = ("frontend", "backend", "mcp", "mongo", "redis")
RELEASE_PORTS: Mapping[str, int] = {
    "frontend": 3000,
    "backend": 8000,
    "mcp": 8001,
    "mongo": 27017,
    "redis": 6379,
}
# The dev instance shifts every release default, agents included.
DEV_OFFSET = 100
_OTHER: Mapping[Instance, Instance] = {"release": "dev", "dev": "release"}
_PUBLIC_URL_FIELDS = ("api_base_url", "server_url")


@dataclass(frozen=True, slots=True)
class Ports:
    """Effective host ports of one instance."""

    instance: Instance
    services: Mapping[str, int]
    agents: Mapping[str, int]
    # Field names (``frontend``, ``agents.<service>``) that use the default.
    from_defaults: frozenset[str]

    def fields(self) -> list[tuple[str, int]]:
        """Every port with its field name under ``instances.<instance>.ports``."""
        return [
            *self.services.items(),
            *((f"agents.{name}", port) for name, port in self.agents.items()),
        ]


def manifest_agents() -> dict[str, int]:
    """Enabled default agents as Compose service name to container port."""
    data = yaml.safe_load(data_file(MANIFEST).read_text(encoding="utf-8")) or {}
    return {
        spec["service"]: spec["port"]
        for spec in (data.get("agents") or {}).values()
        if spec and spec.get("enabled", True)
    }


def _overrides(config: RuntimeConfig | None, instance: Instance) -> InstancePorts:
    settings = (
        getattr(config.instances, instance) if config and config.instances else None
    )
    return settings.ports if settings is not None else InstancePorts()


def effective_ports(
    config: RuntimeConfig | None, instance: Instance, agents: Mapping[str, int]
) -> Ports:
    """Overrides over defaults; ``agents`` is the manifest this CLI ships with."""
    offset = DEV_OFFSET if instance == "dev" else 0
    overrides = _overrides(config, instance)
    services: dict[str, int] = {}
    defaults: set[str] = set()
    for name in SERVICES:
        value = getattr(overrides, name)
        if value is None:
            defaults.add(name)
            value = RELEASE_PORTS[name] + offset
        services[name] = value
    agent_ports: dict[str, int] = {}
    for name, port in agents.items():
        if name in overrides.agents:
            agent_ports[name] = overrides.agents[name]
        else:
            defaults.add(f"agents.{name}")
            agent_ports[name] = port + offset
    # The other instance may run a different manifest; its overrides count as given.
    for name, port in overrides.agents.items():
        agent_ports.setdefault(name, port)
    return Ports(instance, services, agent_ports, frozenset(defaults))


def validate_ports(
    config: RuntimeConfig, instance: Instance, agents: Mapping[str, int]
) -> tuple[Ports, Ports]:
    """Return (running, other) effective ports, or raise naming the clash.

    Both instances may run at once, so no host port may repeat within or across
    them. Agent keys are checked against the manifest only for the running
    instance; the other instance's CLI may ship a different manifest.
    """
    unknown = sorted(set(_overrides(config, instance).agents) - set(agents))
    if unknown:
        raise RuntimeConfigurationError(
            f"instances.{instance}.ports.agents.{unknown[0]}: not a default agent "
            "of this install."
        )
    running = effective_ports(config, instance, agents)
    other = effective_ports(config, _OTHER[instance], agents)
    seen: dict[int, str] = {}
    for ports in (running, other):
        for field, port in ports.fields():
            name = f"instances.{ports.instance}.ports.{field}"
            if port in seen:
                raise RuntimeConfigurationError(
                    f"{name}: {port} is already used by {seen[port]}."
                )
            seen[port] = name
    return running, other


def instance_ports(
    instance: Instance, runtime: RuntimeConfigStore, agents: Mapping[str, int]
) -> tuple[Ports, str | None]:
    """Ports for status and display; never fails on a missing or bad config.

    Bare ``hybro`` opens the TUI before setup, when no config.json exists, so a
    display path falls back to the defaults instead of raising.
    """
    if not (runtime.home / "config.json").is_file():
        return effective_ports(None, instance, agents), None
    try:
        config = runtime.read_config()
    except (RuntimeConfigurationError, OSError):
        return (
            effective_ports(None, instance, agents),
            "config.json is invalid; showing default ports. Run hybro config check.",
        )
    return effective_ports(config, instance, agents), None


def mcp_url(ports: Ports) -> str:
    return _mcp_address(ports.services["mcp"])


def frontend_origin(ports: Ports) -> str:
    return f"http://localhost:{ports.services['frontend']}"


def derive_public(
    frontend: Mapping[str, object], ports: Ports
) -> tuple[dict[str, str], list[str]]:
    """Public URLs for this instance, and warnings about configured ones.

    A configured loopback URL is superseded by the derived one, since a shared
    value would point both instances at one port. Another host is an explicit
    public URL and is kept.
    """
    public = {
        "api_base_url": f"http://localhost:{ports.services['backend']}",
        "server_url": frontend_origin(ports),
        "mcp_url": mcp_url(ports),
    }
    warnings = []
    for field in _PUBLIC_URL_FIELDS:
        value = frontend.get(field)
        if not isinstance(value, str):
            continue
        if urlsplit(value).hostname in LOOPBACK_HOSTS:
            warnings.append(
                f"Ignoring frontend.{field}: it is derived from the instance ports; "
                "delete it from config.json."
            )
        else:
            public[field] = value
            warnings.append(
                f"frontend.{field} overrides the derived URL for both instances."
            )
    return public, warnings


def _variable(service: str) -> str:
    return "HYBRO_PORT_" + service.upper().replace("-", "_")


def transport_environment(running: Ports, other: Ports) -> dict[str, str]:
    """Allowlisted Compose interpolation values for the running instance."""
    env = {_variable(name): str(port) for name, port in running.services.items()}
    env.update({_variable(name): str(port) for name, port in running.agents.items()})
    env["HYBRO_FRONTEND_ORIGIN"] = frontend_origin(running)
    # Each backend scans host ports for agents; skipping both instances' agents
    # keeps it from registering either stack's agents a second time.
    excluded = sorted({*running.agents.values(), *other.agents.values()})
    env["HYBRO_DISCOVERY_EXCLUDED_PORTS"] = ",".join(map(str, excluded))
    return env


def is_transport_variable(name: str) -> bool:
    """Names this module owns; inherited values are never passed through."""
    return name.startswith("HYBRO_PORT_") or name in {
        "HYBRO_FRONTEND_ORIGIN",
        "HYBRO_DISCOVERY_EXCLUDED_PORTS",
    }
