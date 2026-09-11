<div align="center">
  <a href="https://hybro.ai">
    <picture align="center">
      <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.svg">
      <source media="(prefers-color-scheme: light)" srcset="assets/logo-light.svg">
      <img src="assets/logo-dark.svg" alt="Hybro AI" width="500">
    </picture>
  </a>

  <p>
    The open-source agent interoperability platform.<br />
  </p>

  <p>
    <a href="https://opensource.org/licenses/Apache-2.0"><img src="https://img.shields.io/badge/License-Apache%202.0-orange.svg" alt="License"></a>
    <a href="https://x.com/HybroAI"><img src="https://img.shields.io/badge/Follow%20on%20X-000000?logo=x&logoColor=white&style=for-the-badge" alt="Follow on X"></a>
    <a href="https://www.linkedin.com/company/hybroai"><img src="https://img.shields.io/badge/Follow%20on%20LinkedIn-0A66C2?logo=linkedin&logoColor=white&style=for-the-badge" alt="Follow on LinkedIn"></a>
    <a href="https://discord.gg/2S5pCKzUmJ"><img src="https://img.shields.io/badge/Join%20our%20Discord-5865F2?logo=discord&logoColor=white&style=for-the-badge" alt="Join our Discord"></a>
  </p>
</div>

<p align="center">
 ⭐ <em>Star this repo to support the growing Hybro open-source community!</em>
</p>

Hybro AI is an open-source, hybrid multi-agent platform built for seamless agent interoperability. It serves as the core orchestration engine powering the Hybro Agent Network—enabling local and remote AI agents to communicate, collaborate, and execute complex workflows.

## Overview
Hybro AI allows developers and teams to deploy, coordinate, and inspect clusters of autonomous AI agents. Powered by an async FastAPI backend and an interactive Next.js dashboard, Hybro provides real-time agent visualization, execution room management, and protocol-agnostic message routing via the Agent2Agent (A2A) standard.

### Key Features
- **Hybrid Agent Execution**: Seamlessly connect and orchestrate local on-device agents and remote cloud-hosted services.
- **Native Agent Interoperability**: Built around the open Agent2Agent (A2A) protocol for standardized inter-agent communication.
- **Multi-Agent Execution Rooms**: Group specialized agents in dedicated execution rooms to solve multi-step tasks collaboratively.
- **Real-Time Streaming & Inspection**: Live SSE message streaming, multi-agent turn timelines, and an interactive A2A Agent Inspector for testing agent capabilities.
- **CLI-Managed Configuration**: Configure models and authentication once, then start backend, frontend and Agents without maintaining environment files.


## Getting Started

### Prerequisites
- Docker with Compose v2.24+ (`docker compose`; the v1 `docker-compose` binary is not supported)
- [uv](https://docs.astral.sh/uv/) for host-side `hybro setup`
- Node.js 20.19+ (if running the frontend outside of Docker)
- Python 3.12+ and MongoDB 4.2+ (if running the backend outside of Docker; Docker Compose uses MongoDB 7.0)

### Quick Start (Docker)
The installation script clones the repository and opens the CLI when a terminal is available; otherwise it prints setup/start instructions.

```bash
curl -fsSL https://raw.githubusercontent.com/hybroai/hybro/main/install.sh | sh
```

Alternatively, you can manually clone and run:

```bash
git clone https://github.com/hybroai/hybro.git
cd hybro
./scripts/hybro setup
./scripts/hybro start --build
```

- **Hybro App**: http://localhost:3000
- **API Server**: http://localhost:8000

Existing installations should perform the one-time JSON migration below before
removing old environment files.

## Configuration

### Configuration contract

Backend, frontend and bundled-agent user settings belong to one local JSON
configuration, not repository environment files:

```text
~/.hybro/
├── config.json   # User settings; edit through the CLI or directly
└── auth.json     # Credentials; managed through the CLI
```

`HYBRO_HOME` is the sole directory override and must be an absolute path.
Defaults, types and validation remain in a thin code loader; JSON only needs
user overrides. `backend/common/config/loader.py` replaces the old `settings.py`
environment-loading path. Startup validates syntax, known fields, types, ranges and
required combinations. Missing model setup must direct the user to `hybro setup`;
invalid configuration must not silently select a legacy Provider.

CLI edits use the same validation before saving. Manual JSON edits are validated
at the next startup. Settings are a startup snapshot: restart services after
changes, and rebuild the frontend when build-time public settings change.

`.env`, `.env.example` and `frontend/.env.local` are not required by this contract.
The CLI supplies only scoped values to containers and frontend builds; transport
environment variables do not become another user configuration source. Secrets
must never enter the browser bundle or be forwarded wholesale to Agents.

### Edit configuration

```bash
./scripts/hybro config show
./scripts/hybro config check
./scripts/hybro config set backend.log_level '"DEBUG"'
./scripts/hybro config set frontend.max_message_length 12000
./scripts/hybro start --build --recreate
```

Use `config secret <name>` for hidden service-credential input and `setup` for
Provider/model changes. Startup generates missing internal tokens in `auth.json`
without rotating existing values. Backend overrides live under `backend`, public
frontend overrides under `frontend`, and image output size is `image_size`.

### Existing installations

If you previously ran `hybro setup` and have a `~/.hybro/config.yaml`, convert
it once (optionally importing known deployment values from the old root `.env`):

```bash
./scripts/hybro config migrate --from-env .env
./scripts/hybro config check
./scripts/hybro start --build --recreate
```

Omit `--from-env` if no deployment values need importing. Migration preserves
Provider credentials and imports known deployment settings and service secrets;
it refuses to overwrite an existing `config.json`. Original environment files,
`.env.example` and `config.yaml` are not deleted.

Installations that only used the old root `.env` (no YAML setup) must instead
re-run the interactive setup, then start:

```bash
./scripts/hybro setup
./scripts/hybro start --build --recreate
```

After either path, normal setup/start uses JSON only, even if the old files
remain. `migrate` warns when it skips legacy route/model fields it cannot map.

The already-implemented Agent proxy shares backend's setup-selected gateway.
Agents retain SDKs, tools, HITL and A2A execution. Image generation requires an
eligible image model; ChatGPT OAuth alone does not enable it.



## Running

`./scripts/hybro` is the day-2 lifecycle CLI. Common commands:

```bash
./scripts/hybro start                    # up -d, no rebuild (fast daily loop)
./scripts/hybro start --build            # rebuild images (after code/deps change)
./scripts/hybro start --recreate         # recreate containers (runtime configuration changes)
./scripts/hybro start --build --recreate # rebuild+recreate (public frontend / image changes)
./scripts/hybro logs backend             # stream one service (or all if no arg)
./scripts/hybro status                   # docker compose ps --all
./scripts/hybro stop                     # stop but keep containers
./scripts/hybro down                     # remove containers + default network
```

Run `./scripts/hybro --help` for the full subcommand reference. Start through the
CLI so Compose receives validated scoped projections, not ambient dotenv values.

## Architecture
This repository is the source of truth for the product. Its frontend and backend
are the in-repository `frontend/` and `backend/` directories used by Docker
Compose and CI.

The repository is split into these primary components:
- `backend/`: A FastAPI orchestration engine using MongoDB for persistence and optional Redis services for cross-process coordination.
- `frontend/`: A Next.js 16 (Turbopack) application for chat, local agent discovery, agent management, and inspection.
- `default_agents/`: A collection of ready-to-use A2A agents, each running as its own container, plus a one-shot `registrar` that registers them with the backend on startup.

## API keys
Use `hybro setup` for OpenAI API Key or ChatGPT OAuth, DeepSeek API Key, or
Anthropic API Key.
Agents receive a scoped `HYBRO_AGENT_CONFIG` projection containing the backend
proxy URL, a separate inference token and image size. They pass these values
explicitly to their SDKs. The token cannot authenticate as a user or registrar;
it is required even under mock auth. Agents never receive Provider credentials
or the setup directory.
See [gateway architecture](backend/docs/System-Architecture.md#llm_gateway) for
the supported proxy subset and limits.

## Contributing
We welcome contributions from the community! Whether you are fixing a bug, adding a feature, or improving documentation, please feel free to open a pull request.

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'feat: add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License
Apache License 2.0
