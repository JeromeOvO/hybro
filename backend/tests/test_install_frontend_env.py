from __future__ import annotations

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ensure_frontend_env.sh"


def _run_helper(root_env: Path, frontend_env: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(SCRIPT), str(root_env), str(frontend_env)],
        check=True,
        capture_output=True,
        text=True,
    )


def test_writes_only_frontend_keys(tmp_path: Path) -> None:
    root_env = tmp_path / ".env"
    frontend_env = tmp_path / "frontend" / ".env.local"
    root_env.write_text(
        "\n".join(
            [
                "OPENAI_API_KEY=sk-secret",
                "WEBHOOK_SIGNING_KEY=" + ("x" * 32),
                "DEFAULT_AGENT_REGISTRAR_TOKEN=regtoken",
                "AGENT_REGISTRAR_TOKEN=regtoken",
                "MONGODB_URL=mongodb://secret",
                "REDIS_URL=redis://secret",
                "CLERK_SECRET_KEY=clerk_secret",
                "CLERK_WEBHOOK_SECRET=whsec",
                "NEXT_PUBLIC_API_BASE_URL=http://localhost:8000",
                "NEXT_PUBLIC_ENABLE_WAITLIST=true",
                "E2E_CLERK_USER_EMAIL=dev@example.com",
                "LEAD_AI_MODEL=gpt-5-mini",
                "",
            ]
        )
    )

    result = _run_helper(root_env, frontend_env)
    text = frontend_env.read_text()

    assert "Wrote filtered frontend env" in result.stdout
    assert "NEXT_PUBLIC_API_BASE_URL=http://localhost:8000" in text
    assert "CLERK_SECRET_KEY=clerk_secret" in text
    assert "E2E_CLERK_USER_EMAIL=dev@example.com" in text
    assert "OPENAI_API_KEY" not in text
    assert "WEBHOOK_SIGNING_KEY" not in text
    assert "DEFAULT_AGENT_REGISTRAR_TOKEN" not in text
    assert "MONGODB_URL" not in text
    assert "LEAD_AI_MODEL" not in text
