# Hybro Backend

This directory is the canonical backend for the Hybro monorepo. The retired
standalone `multi-agents-backend` repository is not the source used by the
current application, Docker Compose configuration, or CI.

## Run from the monorepo

### Docker Compose

From the repository root:

```sh
docker compose up -d --build
```

The API is available at <http://localhost:8000> and its health endpoint is
<http://localhost:8000/health>.

### Run the backend directly

Python 3.12+ and a reachable MongoDB instance are required.

```sh
cd backend
cp .env.example .env
uv sync --extra dev
uv run uvicorn main:app --reload
```

Use `AUTH_MODE=mock` for local development without Clerk credentials. Redis is
optional for a single-process local server; cross-process delivery and locking
require it.

## Project layout

- `main.py`: FastAPI application and lifespan entry point.
- `container.py`: runtime composition root.
- `api_gateway/`: the only HTTP route package.
- `agent/`, `room/`, `execution/`, `context_memory/`, `delivery/`: domain modules.
- `a2a_adapter/`: A2A SDK boundary.
- `llm_gateway/`: LLM provider boundary.
- `hub_runtime_bridge/`: local Hub relay runtime.
- `dal/`: MongoDB and Redis adapters.
- `room_files/`: room-owned file metadata and local content storage.
- `tests/`: unit, boundary, and workflow tests.

The former `api/` compatibility package has been removed. New routes belong in
`api_gateway/routes/` and must use injected owner protocols rather than concrete
repositories.

See [`docs/System-Architecture.md`](docs/System-Architecture.md) for the current
runtime architecture. `docs/MODULAR_DECOUPLING_DESIGN.md` is an archived design
record and must not be treated as a description of the live system.

## Validation

Run from `backend/`:

```sh
uv run ruff format --check .
uv run ruff check .
uv run pytest
```

## A2A inline file limits

`A2A_INLINE_FILE_MAX_RAW_BYTES` limits one uploaded file before base64 encoding.
`A2A_INLINE_MESSAGE_MAX_ENCODED_BYTES` limits aggregate encoded file bytes in an
outbound A2A message. Uploaded files sent to agents use inline A2A bytes; local
filesystem paths and authenticated room-file URLs remain internal to Hybro.
