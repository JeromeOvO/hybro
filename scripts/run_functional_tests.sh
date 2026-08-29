#!/usr/bin/env bash
# ==============================================================================
# Functional Test Suite Runner for Hybro
# Runs Backend Functional & E2E Validation Suites
# ==============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="${REPO_ROOT}/backend"
FRONTEND_DIR="${REPO_ROOT}/frontend"

echo "=== 1. Running Backend Functional Regression Tests ==="
cd "${BACKEND_DIR}"
uv run pytest tests/functional/ -v --tb=short

echo "=== 2. Running Frontend Playwright E2E Functional Tests ==="
cd "${FRONTEND_DIR}"
npx playwright test tests/e2e/functional-hitl-workflow.spec.ts

echo "=== All Functional Tests Completed Successfully ==="
