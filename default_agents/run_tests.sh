#!/usr/bin/env bash
# Verify each default agent end-to-end against a running stack.
#
# Prerequisites:
#   - The stack is up:  docker compose up -d --build
#   - OPENAI_API_KEY is exported (for the functional checks); otherwise those
#     checks are skipped.
set -euo pipefail

cd "$(dirname "$0")/.."

# Extract OPENAI_API_KEY from the repo-root .env WITHOUT sourcing the file as
# shell code. The consolidated .env holds backend and frontend secrets whose
# values may legitimately contain characters that shell would try to expand or
# execute ($, backticks, command substitutions, ...). We only need this one
# key, so parse just that one line.
if [ -z "${OPENAI_API_KEY:-}" ] && [ -f .env ]; then
    if command -v uv >/dev/null 2>&1; then
        parsed_key=$(uv run --with python-dotenv python -c 'from dotenv import dotenv_values; print(dotenv_values(".env").get("OPENAI_API_KEY", ""))' 2>/dev/null || true)
    else
        parsed_key=$(python -c 'from dotenv import dotenv_values; print(dotenv_values(".env").get("OPENAI_API_KEY", ""))' 2>/dev/null || true)
    fi
    if [ -n "$parsed_key" ]; then
        OPENAI_API_KEY=$parsed_key
        export OPENAI_API_KEY
    fi
    unset parsed_key
fi

cd default_agents

echo "Running default agent verification tests..."
echo "  BACKEND_URL=${BACKEND_URL:-http://localhost:8000}"
echo "  AGENT_HOST=${AGENT_HOST:-localhost}"

# `python -m pytest` puts the current directory (default_agents/) on sys.path,
# so tests/ can import sibling modules such as load_repo_env. The bare `pytest`
# console script does NOT add cwd to sys.path and fails with ModuleNotFoundError
# on this layout.
if command -v uv >/dev/null 2>&1; then
    uv run --with pytest --with pytest-asyncio --with 'a2a-sdk>=0.2.6,<0.3.0' --with langchain --with langchain-core --with langchain-openai --with requests --with pyyaml --with python-dotenv python -m pytest -v tests
else
    python -m pytest -v tests
fi
