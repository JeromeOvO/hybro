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
- **Node.js**: 20.9+
- **Python**: 3.12+
- **`uv`**: Installed (`pip install uv` or via `curl -LsSf https://astral.sh/uv/install.sh | sh`)
- **Docker & Docker Compose**: For local Redis & MongoDB

### Backend (`backend/`)
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
# Install dependencies
npm install

# Run dev server (Turbopack)
npm run dev

# Run tests & linters
npm run lint
npm run test
```

### Running with Docker Compose
```bash
docker compose up -d --build
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
