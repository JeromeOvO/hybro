"""
A2A Agent Server for Weather Agent

This module exposes the LangChain Weather Agent as an A2A (Agent-to-Agent) Protocol
compliant agent using the a2a-adapter SDK (v0.2) with the LangChainAdapter.

Features:
- Get current weather for any city
- Get weather forecasts

Usage:
    python a2a_main.py

    Or with environment variables:
    SERVER_PORT=7001 python a2a_main.py

Input Examples:
    - "What's the weather in New York?"
    - "Give me the forecast for Tokyo"
    - "How's the weather in London and Paris?"
"""

import os
from pathlib import Path

from a2a_adapter import load_adapter, build_agent_card, serve_agent

# Support both direct execution and module execution
try:
    from .weather_agent import create_weather_agent
except ImportError:
    from weather_agent import create_weather_agent

try:
    from load_repo_env import load_repo_env
except ImportError:  # Host run: helper lives in default_agents/
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        from load_repo_env import load_repo_env
    except ImportError:  # Wheel install: helper is not packaged with the agent.
        from dotenv import load_dotenv

        def load_repo_env(*, start=None):
            load_dotenv()


load_repo_env(start=Path(__file__))

# Configuration from environment
SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("SERVER_PORT", "7001"))
SERVER_DOMAIN = os.getenv("SERVER_DOMAIN", "localhost")


def main():
    """Main entry point - setup agent and start server."""
    # Create the LangChain weather agent (using OpenAI)
    weather_agent = create_weather_agent(temperature=0)

    # Load the LangChain adapter using a2a-adapter SDK
    adapter = load_adapter(
        {
            "adapter": "langchain",
            "runnable": weather_agent,
            "input_key": "input",
            "output_key": "output",
            "name": "Weather Agent",
            "description": (
                "LangChain-powered weather assistant. Ask about current weather "
                "conditions or forecasts for any city."
            ),
            "skills": [
                {
                    "id": "current_weather",
                    "name": "Get Current Weather",
                    "description": "Get the current weather conditions for any city.",
                    "tags": ["weather", "current", "temperature"],
                    "examples": [
                        "What's the weather in New York?",
                        "How's the weather in London?",
                    ],
                },
                {
                    "id": "weather_forecast",
                    "name": "Get Weather Forecast",
                    "description": "Get weather forecast for the next few days.",
                    "tags": ["weather", "forecast", "prediction"],
                    "examples": [
                        "Give me the forecast for Tokyo",
                        "What's the 5-day forecast for Paris?",
                    ],
                },
            ],
        }
    )

    agent_url = f"http://{SERVER_DOMAIN}:{SERVER_PORT}"
    agent_card = build_agent_card(adapter, url=agent_url)

    print("=" * 60)
    print("Weather Agent - A2A Server")
    print("=" * 60)
    print(f"Server URL: {agent_url}")
    print(f"Port: {SERVER_PORT}")
    print(f"Agent Card: {agent_url}/.well-known/agent.json")
    print("=" * 60)
    print("\nExample queries:")
    print('  - "What\'s the weather in New York?"')
    print('  - "Give me the forecast for Tokyo"')
    print('  - "How\'s the weather in London?"')
    print("\nStarting server...")

    serve_agent(adapter, agent_card=agent_card, host=SERVER_HOST, port=SERVER_PORT)


if __name__ == "__main__":
    main()
