#!/usr/bin/env python3
"""Entry point for the multi-agents backend application."""

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True) 