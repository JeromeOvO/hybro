"""Host configuration and Compose entry point. Environment files are migration input only."""

from __future__ import annotations

import argparse
import getpass
import io
import json
import os
import stat
import subprocess
import sys
import warnings
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import ValidationError

from common.config.loader import (
    PRIVATE_FIELDS,
    ROUTE_FIELDS,
    FrontendSettings,
    Settings,
)
from llm_gateway.runtime_config import RuntimeConfigurationError, resolve_credential
from llm_gateway.runtime_store import (
    SERVICE_KEYS,
    RuntimeConfigStore,
    _auth_object,
    _credential_bytes,
    parse_config,
    parse_credential,
    runtime_home,
)

ROOT = Path(__file__).resolve().parents[3]


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise RuntimeConfigurationError("Invalid command arguments; run hybro --help.")


def store() -> RuntimeConfigStore:
    return RuntimeConfigStore(runtime_home(os.environ))


def compose_environment(runtime: RuntimeConfigStore, *, start: bool) -> dict[str, str]:
    # Preserve ordinary executable/socket/proxy context, not legacy application settings.
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {key.upper() for key in Settings.model_fields}
        and not k.startswith("NEXT_PUBLIC_")
    }
    for key in (
        "CLERK_SECRET_KEY",
        "CLERK_WEBHOOK_SECRET",
        "AGENT_REGISTRAR_TOKEN",
        "DEFAULT_AGENT_LLM_TOKEN",
        "HYBRO_AGENT_CONFIG",
        "IMAGE_SIZE",
        "OPENAI_MODEL",
        "IMAGE_MODEL",
        "COMPOSE_FILE",
        "COMPOSE_ENV_FILES",
        "COMPOSE_PROJECT_NAME",
    ):
        env.pop(key, None)
    env["HYBRO_HOME"] = str(runtime.home)
    env["COMPOSE_DISABLE_ENV_FILE"] = "1"
    if not start:
        return env
    state = runtime.load({})
    from llm_gateway.catalog import validate_models

    validate_models(state.config)
    secrets = runtime.ensure_service_credentials()
    if any(
        key in state.config.backend and key in secrets
        for key in ("mongodb_url", "redis_url")
    ):
        raise RuntimeConfigurationError(
            "Specify each connection URL in config.json or auth.json, not both."
        )
    backend = Settings.model_validate(
        {
            **state.config.backend,
            **{k: v for k, v in secrets.items() if k in Settings.model_fields},
        }
    )
    if backend.auth_mode == "clerk" and not backend.clerk_secret_key:
        raise RuntimeConfigurationError("Clerk mode requires credentials in auth.json.")
    public = FrontendSettings.model_validate(
        {"api_prefix": backend.api_prefix, **state.config.frontend}
    )
    env.update(
        {
            "HYBRO_API_PREFIX": backend.api_prefix,
            "HYBRO_FRONTEND_CONFIG": public.model_dump_json(),
            "HYBRO_AGENT_CONFIG": json.dumps(
                {
                    "base_url": f"http://backend:8000{backend.api_prefix}/internal/llm",
                    "token": secrets["default_agent_llm_token"],
                    "image_size": state.config.image_size,
                }
            ),
            "AGENT_REGISTRAR_TOKEN": secrets["default_agent_registrar_token"],
            "CLERK_SECRET_KEY": secrets.get("clerk_secret_key", ""),
            "CLERK_WEBHOOK_SECRET": secrets.get("clerk_webhook_secret", ""),
        }
    )
    return env


def compose(arguments: list[str], *, start: bool = False) -> int:
    runtime = store()
    env = compose_environment(runtime, start=start)
    if start:
        result = subprocess.run(
            [sys.executable, str(ROOT / "default_agents/render_compose.py")],
            cwd=ROOT,
            env=env,
            check=False,
        )
        if result.returncode:
            return result.returncode
    return subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            os.devnull,
            "-f",
            str(ROOT / "docker-compose.yml"),
            *arguments,
        ],
        cwd=ROOT,
        env=env,
        check=False,
    ).returncode


def service_status() -> list[dict[str, object]]:
    """Read only Compose container metadata; never load application credentials."""
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            os.devnull,
            "-f",
            str(ROOT / "docker-compose.yml"),
            "ps",
            "--all",
            "--orphans=false",
            "--format",
            "json",
        ],
        cwd=ROOT,
        env=compose_environment(store(), start=False),
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    raw = result.stdout.strip()
    rows = (
        json.loads(raw)
        if raw.startswith("[")
        else [json.loads(line) for line in raw.splitlines()]
    )
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Invalid container status")
    return rows


def _import_environment(config, auth, values):
    backend = dict(config.backend)
    public = dict(config.frontend)
    services = dict(_auth_object(auth).get("services", {}))
    defaults = Settings()
    for name, value in values.items():
        key = name.lower()
        if key in SERVICE_KEYS and (
            key not in {"mongodb_url", "redis_url"}
            or urlsplit(value).username
            or urlsplit(value).password
        ):
            services[key] = value
        elif name == "AGENT_REGISTRAR_TOKEN":
            services.setdefault("default_agent_registrar_token", value)
        elif key in Settings.model_fields and key not in PRIVATE_FIELDS | ROUTE_FIELDS:
            parsed = Settings.model_validate({key: value}, strict=False)
            normalized = parsed.model_dump(mode="json")[key]
            # These were host hints which Compose previously overrode unconditionally.
            if key == "mongodb_url" and value in {
                "localhost:27017",
                "mongodb://127.0.0.1:27017/?replicaSet=rs0",
                "mongodb://localhost:27017/?replicaSet=rs0",
            }:
                continue
            if normalized != getattr(defaults, key):
                backend[key] = normalized
        elif name.startswith("NEXT_PUBLIC_"):
            field = name.removeprefix("NEXT_PUBLIC_").lower()
            if field in FrontendSettings.model_fields:
                parsed = FrontendSettings.model_validate({field: value}, strict=False)
                public[field] = getattr(parsed, field)
    return backend, public, services


def _read_legacy_environment(path: str) -> dict[str, str]:
    from dotenv import dotenv_values

    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise RuntimeConfigurationError(
                "Legacy environment input must be a regular file."
            )
        data = stream.read(64 * 1024 + 1)
    if len(data) > 64 * 1024:
        raise RuntimeConfigurationError("Legacy environment input exceeds 64 KiB.")
    return {
        key: value
        for key, value in dotenv_values(
            stream=io.StringIO(data.decode("utf-8")), interpolate=False
        ).items()
        if value
    }


def migrate(runtime: RuntimeConfigStore, env_path: str | None) -> None:
    """Explicit one-time conversion; normal startup never reads these inputs."""
    import yaml

    with runtime._locked():
        if runtime._read("config.json") is not None:
            raise RuntimeConfigurationError(
                "config.json already exists; migration will not overwrite it."
            )
        raw = runtime._read("config.yaml")
        if raw is None:
            raise RuntimeConfigurationError(
                "No legacy config.yaml found; run hybro setup."
            )
        # Legacy setup wrote a small mapping, never tags/aliases or merge keys.
        try:
            if any(
                isinstance(event, yaml.events.AliasEvent) for event in yaml.parse(raw)
            ):
                raise RuntimeConfigurationError(
                    "Legacy YAML aliases are not supported."
                )
            document = yaml.safe_load(raw)
        except yaml.YAMLError:
            raise RuntimeConfigurationError(
                "Invalid legacy config.yaml; no files changed."
            ) from None
        config = parse_config(json.dumps(document, allow_nan=False).encode())
        auth = runtime._read("auth.json")
        credential = parse_credential(auth) if auth is not None else None
        values = _read_legacy_environment(env_path) if env_path else {}
        credential = resolve_credential(
            config, credential, values if credential is None else {}
        ).credential
        backend, public, services = _import_environment(config, auth, values)
        document = config.model_dump(exclude_none=True)
        document.update(backend=backend, frontend=public)
        if values.get("IMAGE_SIZE"):
            document["image_size"] = values["IMAGE_SIZE"]
        encoded = (json.dumps(document, indent=2, allow_nan=False) + "\n").encode()
        parse_config(encoded)
        auth_document = _auth_object(auth)
        auth_document["services"] = services
        runtime._save_pair(
            encoded,
            _credential_bytes(credential, json.dumps(auth_document).encode()),
            None,
            auth,
        )
    print(
        "Migrated config.json/auth.json. Original files were not removed. Restart services to apply."
    )


def config_command(arguments: list[str]) -> int:
    parser = Parser(prog="hybro config")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("show")
    sub.add_parser("check")
    edit = sub.add_parser("set")
    edit.add_argument("path")
    edit.add_argument("value", help="JSON value; credentials are not accepted here")
    secret = sub.add_parser("secret")
    secret.add_argument("name", choices=sorted(SERVICE_KEYS))
    migration = sub.add_parser("migrate")
    migration.add_argument(
        "--from-env", help="Explicit legacy deployment file to import once"
    )
    args = parser.parse_args(arguments)
    runtime = store()
    if args.command == "migrate":
        migrate(runtime, args.from_env)
    elif args.command == "set":
        runtime.update_config(args.path.split("."), json.loads(args.value))
        print(
            "Configuration saved. Restart services; rebuild frontend if public build settings changed."
        )
    elif args.command == "secret":
        if not sys.stdin.isatty():
            raise RuntimeConfigurationError(
                "Secret input requires an interactive terminal."
            )
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            try:
                value = getpass.getpass("Credential (hidden): ")
            except getpass.GetPassWarning:
                raise RuntimeConfigurationError(
                    "Cannot read a hidden credential from this terminal."
                ) from None
        if not value.strip():
            raise RuntimeConfigurationError("Credential must not be blank.")
        if (
            args.name
            in {
                "default_agent_registrar_token",
                "default_agent_llm_token",
                "webhook_signing_key",
            }
            and len(value.encode()) < 32
        ):
            raise RuntimeConfigurationError(
                "Internal credentials must be at least 32 bytes."
            )
        with runtime._locked():
            data = runtime._read("auth.json")
            credential = parse_credential(data) if data else None
            document = _auth_object(data)
            document.setdefault("services", {})[args.name] = value
            runtime._replace(
                "auth.json",
                _credential_bytes(credential, json.dumps(document).encode()),
            )
        print("Credential saved. Restart affected services.")
    elif args.command == "show":
        print(runtime.read_config().model_dump_json(indent=2))
    else:
        runtime.load({})
        print("Configuration and credential structure valid; no Provider request made.")
    return 0


def _start(arguments: list[str]) -> int:
    parser = Parser(prog="hybro start")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--recreate", action="store_true")
    options = parser.parse_args(arguments)
    params = ["up", "-d", "--remove-orphans"]
    if options.build:
        params.append("--build")
    if options.recreate:
        params.append("--force-recreate")
    return compose(params, start=True)


def _logs(arguments: list[str]) -> int:
    if arguments[:1] != ["--container"]:
        return compose(["logs", "-f", *arguments])
    parser = Parser(prog="hybro logs --container")
    parser.add_argument("name")
    name = parser.parse_args(arguments[1:]).name
    return subprocess.run(
        ["docker", "logs", "--follow", "--tail", "100", "--", name],
        env=compose_environment(store(), start=False),
        check=False,
    ).returncode


def main(arguments: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    command = arguments.pop(0) if arguments else "tui"
    try:
        if command == "config":
            return config_command(arguments)
        if command in {"setup", "tui"}:
            from llm_gateway import cli_tui, setup_cli

            if command == "setup":
                return setup_cli.main(arguments)
            if arguments:
                raise RuntimeConfigurationError("hybro tui does not accept arguments.")
            return cli_tui.main()
        handler = {"start": _start, "up": _start, "logs": _logs}.get(command)
        if handler:
            return handler(arguments)
        commands = {
            "status": ["ps", "--all"],
            "ps": ["ps", "--all"],
            "stop": ["stop"],
            "down": ["down"],
            "restart": ["restart"],
        }
        if command not in commands:
            raise RuntimeConfigurationError("Unknown command; run hybro --help.")
        return compose([*commands[command], *arguments])
    except (RuntimeConfigurationError, ValidationError) as exc:
        print(
            str(exc)
            if isinstance(exc, RuntimeConfigurationError)
            else "Invalid configuration; check field types and ranges.",
            file=sys.stderr,
        )
        return 1
    except (OSError, ValueError, RecursionError):
        print(
            "Configuration or command failed; check JSON syntax and file access.",
            file=sys.stderr,
        )
        return 1
    except (KeyboardInterrupt, EOFError):
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
