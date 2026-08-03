#!/usr/bin/env bash
# Verify each default agent end-to-end against a running stack.
#
# Prerequisites:
#   - The stack is up:  docker compose up -d --build
#   - OPENAI_API_KEY is exported (for the functional checks); otherwise those
#     checks are skipped.
set -euo pipefail

cd "$(dirname "$0")"

# Load OPENAI_API_KEY from default_agents/.env if not already in the environment.
if [ -z "${OPENAI_API_KEY:-}" ] && [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
fi

echo "Running default agent verification tests..."
echo "  BACKEND_URL=${BACKEND_URL:-http://localhost:8000}"
echo "  AGENT_HOST=${AGENT_HOST:-localhost}"

# Prefer uv if available, otherwise fall back to pytest / python -m pytest.
if command -v uv >/dev/null 2>&1; then
    uv run --with pytest --with requests --with pyyaml pytest -v tests
elif command -v pytest >/dev/null 2>&1; then
    pytest -v tests
else
    python -m pytest -v tests
fi
