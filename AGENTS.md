# Repository Guidelines

## Project Structure & Module Organization

This is a Python FastAPI backend for HybroAI's multi-agent system. The app entry points are `main.py`, `__main__.py`, and the console script `multi-agents-backend`. Route adapters live in `api/` and `api_gateway/`; domain modules are organized by capability, including `agent/`, `room/`, `execution/`, `delivery/`, `context_memory/`, `hub_runtime_bridge/`, `platform_module/`, and `llm_gateway/`. Shared contracts are in `common/`, persistence adapters in `dal/` and `database/`, background tasks in `jobs/`, helpers in `scripts/`, and architecture notes in `docs/` and `System-Architecture.md`. Tests live in `tests/`.

## Build, Test, and Development Commands

- `uv sync`: install dependencies from `uv.lock`.
- `uv sync --extra dev`: install runtime and development test dependencies.
- `uvicorn main:app --reload`: run the API locally at `http://localhost:8000`.
- `uv run pytest`: run the full test suite.
- `uv run pytest tests/test_agent_repository.py`: run one focused test file.
- `uv run ruff check .`: lint imports, bugbear rules, pyupgrade checks, and complexity.
- `uv run ruff format .`: format Python code with the configured Ruff formatter.

## Coding Style & Naming Conventions

Use Python 3.11+ and prefer explicit, typed, async-aware code for I/O paths. Ruff targets Python 3.12, uses an 88-character line length, and enforces `E`, `F`, `B`, `I`, `UP`, and `C90` while ignoring `E501`. Name modules and functions with `snake_case`, classes with `PascalCase`, and tests with `test_*`. Keep route/viewset code thin; put business behavior in service, facade, repository, or translator modules.

## Testing Guidelines

Pytest is configured in `pyproject.toml` with `tests/` as the test root, `test_*.py` files, `Test*` classes, and `test_*` functions. Async tests run with `pytest-asyncio` in auto mode. Use markers when helpful: `unit`, `integration`, `slow`, and `asyncio`. Add focused tests next to related coverage patterns, for example `tests/test_api_gateway_*.py` for gateway behavior or `tests/test_module_*.py` for module boundary checks.

## Architecture Documentation

After completing code changes, update architecture documentation. Use `System-Architecture.md` for system-level changes and `docs/` for module-specific decisions, migrations, or design notes.

## Commit & Pull Request Guidelines

Recent history uses short imperative commits plus prefixes such as `docs:`, `chore:`, `test:`, and `refactor(scope):`. Keep messages specific, for example `test: cover relay cancellation path`. Pull requests should include a summary, linked issue or context, test evidence, and API or migration notes when behavior, schemas, or persistence change.

## Security & Configuration Tips

Copy `.env.example` to `.env` for local configuration and never commit secrets; `.env*` is ignored except `.env.example`. Keep generated logs, caches, coverage output, and virtual environments out of commits.
