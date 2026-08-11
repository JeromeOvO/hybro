# Repository Guidelines

## Scope

These instructions apply only to the current `hybro` Git repository. The product frontend and backend referenced below are the in-repository `frontend/` and `backend/` directories. Do not inspect or treat paths outside this repository as product sources unless a task explicitly asks for them.

## Project Structure & Module Organization

This repository is the source of truth for the local Hybro product:

```text
frontend/ -> backend/ -> A2A agents, Hub relay, and external services
```

- `frontend/`: Next.js 16 product UI.
- `backend/`: Python 3.12 FastAPI platform backend.
- `default_agents/`: bundled A2A agents and startup registration tooling.
- `docker-compose.yml`: local product stack.
- `assets/` and `public/`: repository-level static assets.

Follow the more specific instructions in `frontend/AGENTS.md` or `backend/AGENTS.md` when working inside those directories.

## Build, Test, and Development Commands

Run commands from the relevant directory unless noted otherwise.

- Repository root: `docker compose up -d --build`
- After every completed code change in this repository, run `docker compose up -d --build` from the repository root so the local product images and containers are rebuilt with the latest code for manual testing. Treat this as a required final verification step unless the user explicitly asks not to rebuild.
- Frontend: `npm run dev`, `npm run lint`, `npm run test`, `npm run test:e2e`, `npm run build`
- Backend: `uv sync --extra dev`, `uv run pytest`, `uv run ruff check .`, `uv run ruff format .`
- Default agents: use `default_agents/run_tests.sh` for its focused test suite.

## Coding Style & Naming Conventions

Python code targets 3.12+, uses 4-space indentation, public type hints, snake_case modules, and Ruff's configured 88-character line length. TypeScript uses strict mode, React, Next.js, ESLint, and the conventions documented in `frontend/AGENTS.md`. Reuse existing services, repositories, and UI primitives before adding new patterns.

## Testing Guidelines

Add focused tests near the code they cover. Backend tests use Pytest naming (`test_*.py`, `Test*`, and `test_*`). Frontend unit tests use Vitest and browser flows use Playwright. Mock network, LLM, database, wallet, and webhook calls unless testing integration behavior.

## Pre-Commit CI Parity

CI failures in this repository frequently come from backend formatting drift or protocol contract inventories that were not updated with an interface change. Before every commit or push, run the same backend gates CI runs:

```bash
cd backend
uv run ruff format --check .
uv run ruff check .
```

If the format check reports files, run `uv run ruff format .`, then repeat both checks before committing. Do not treat `ruff check` success as proof that `ruff format --check` will pass; they are separate gates.

When adding, removing, or renaming a public method on a `Protocol`, repository port, facade port, or other compatibility interface, search for exact method inventories and architecture contract tests. In particular, update the relevant expected method set in `backend/tests/test_common_foundation.py` and run:

```bash
cd backend
uv run pytest tests/test_common_foundation.py::test_protocol_methods_match_design_doc
```

Also run focused tests for the changed module. For frontend changes, run `npm run lint`, the affected Vitest projects, and `npm run build` before push. Re-check `git diff --check` and the working tree immediately before committing.

## Commit & Pull Request Guidelines

Commit from this repository root. Use short imperative subjects with prefixes such as `feat:`, `fix:`, `test:`, `docs:`, `chore:`, or scoped forms like `fix(ui):`. Pull requests should summarize behavior changes, list tests run, note environment or migration changes, and include screenshots or sample prompts/responses for visible UI or agent changes.

## Security & Configuration Tips

Never commit secrets. Start from `backend/.env.example`, `frontend/.env.example`, or `default_agents/.env.example` as applicable, keep local values in ignored environment files, and document newly required variables.
