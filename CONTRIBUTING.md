# Contributing to Hybro AI

Thank you for your interest in contributing to Hybro AI! We welcome contributions from developers of all skill levels. Whether you are fixing a bug, improving documentation, adding new features, or sharing feedback, your help is appreciated.

---

## Community Guidelines

We expect all contributors and participants to be respectful, constructive, and collaborative when interacting in issues, discussions, and pull requests.

---

## How Can I Contribute?

### 1. Reporting Bugs
Before creating a bug report, please check the [existing issues](https://github.com/hybroai/hybro/issues) to see if it has already been reported.

When reporting a bug, please use the **Bug Report** template and include:
- A clear, descriptive title.
- Steps to reproduce the problem.
- Expected vs. actual behavior.
- Relevant logs, stack traces, or screenshots.
- Your OS, Python/Node versions, and environment details.

### 2. Suggesting Features
Enhancement suggestions are tracked as GitHub issues. When suggesting a feature:
- Use the **Feature Request** template.
- Explain *why* this feature would be useful to users.
- Describe potential implementation details or alternatives considered.

### 3. Submitting Pull Requests
1. **Fork the repository** and create your branch from `main`:
   ```bash
   git checkout -b feature/my-amazing-feature
   ```
2. **Make your changes** following our coding standards.
3. **Run tests** for both backend and frontend to ensure everything passes.
4. **Commit your changes** using conventional commit messages (e.g., `feat: ...`, `fix: ...`, `docs: ...`, `test: ...`).
5. **Push to your fork** and open a Pull Request against `main`.

---

## Local Development Setup

### Prerequisites
- **Node.js**: 20.19+ (the version pinned in `frontend/.nvmrc`)
- **Python**: 3.12+
- **`uv`**: Installed (`pip install uv` or via `curl -LsSf https://astral.sh/uv/install.sh | sh`)
- **Docker with Compose v2.24+**: For the full stack, or for local MongoDB and Redis
- **MongoDB 4.2+**: Only when running the backend outside Docker
- **Redis 7**: Optional when running a single backend process outside Docker

### Full stack
Configure once, then start the stack through the lifecycle CLI:

```bash
./scripts/hybro setup          # writes ~/.hybro/config.json and ~/.hybro/auth.json
./scripts/hybro start --build  # rebuild images from your checkout
```

Do not run `docker compose up` directly. The CLI passes the validated
configuration to the containers and disables Compose dotenv discovery. See
[Running](README.md#running) for the other lifecycle commands.

### Backend (`backend/`)
A backend started on the host reads the same `~/.hybro` configuration (or
`HYBRO_HOME`), so run `./scripts/hybro setup` first. It connects to MongoDB at
`localhost:27017` by default. Redis is disabled unless `backend.redis_url` is
set in `config.json`; without it, SSE broadcast is process-local.

```bash
cd backend
# Install dependencies
uv sync --extra dev

# Run local development server
uv run uvicorn main:app --reload --port 8000

# Run tests
uv run pytest
```

### Frontend (`frontend/`)
```bash
cd frontend
# Install dependencies (use `npm install` only when changing dependencies)
npm ci

# Run dev server (Turbopack)
npm run dev

# Run tests & linters
npm run lint
npm run test
```

---

## Commit Message Conventions

We follow [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` A new feature for the user
- `fix:` A bug fix
- `docs:` Documentation changes
- `style:` Formatting, missing semi-colons, etc. (no functional code changes)
- `refactor:` Code change that neither fixes a bug nor adds a feature
- `test:` Adding or updating tests
- `chore:` Maintenance tasks, dependency updates, CI configuration

---

## License

By contributing to Hybro AI, you agree that your contributions will be licensed under the project's [Apache License 2.0](LICENSE).
