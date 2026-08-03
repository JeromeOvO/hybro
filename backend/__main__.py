#!/usr/bin/env python3
"""Entry point for the multi-agents backend application."""

if __name__ == "__main__":
    import uvicorn

    from common.observability.bootstrap import (
        settings as _logging_settings,  # noqa: F401
    )

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=[
            "api",
            "api_gateway",
            "common",
            "database",
            "execution",
            "jobs",
            "models",
        ],
        reload_excludes=[".*", "*.pyc", "__pycache__"],
        log_config=None,
        access_log=False,
    )
