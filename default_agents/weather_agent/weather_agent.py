"""
Weather Agent using LangChain with Tool Calling (OpenAI)

This module creates a weather agent that can be:
1. Run directly for testing
2. Imported and used by a2a_main.py for A2A exposure

The agent accepts a city name and returns weather information.
"""

import os
from datetime import date
from pathlib import Path
from typing import Any

import requests
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableSerializable

try:
    from load_repo_env import load_repo_env
except ImportError:  # Host run: helper lives in default_agents/
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        from load_repo_env import load_repo_env
    except ImportError:  # Wheel install: helper is not packaged with the agent.
        # The monorepo helper only ships with the repo checkout. When the agent
        # is installed as a standalone wheel (e.g. `pip install weather-agent`),
        # fall back to the standard python-dotenv discovery so we still honour
        # any .env python-dotenv finds walking up from cwd (or no-op otherwise).
        # Docker Compose already injects process env, so this branch matters
        # only for third-party pip installs.
        from dotenv import load_dotenv

        def load_repo_env(*, start=None):
            load_dotenv()


load_repo_env(start=Path(__file__))


# ============ Real weather provider (Open-Meteo) ============
# Open-Meteo is a free, no-API-key weather service. We geocode the city name,
# then fetch current conditions / a daily forecast for those coordinates.

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_HTTP_TIMEOUT = 10  # seconds
_MAX_FORECAST_DAYS = 16  # Open-Meteo daily forecast horizon

# WMO weather interpretation codes -> human-readable description.
WMO_CODES = {
    0: "clear sky",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "depositing rime fog",
    51: "light drizzle",
    53: "moderate drizzle",
    55: "dense drizzle",
    56: "light freezing drizzle",
    57: "dense freezing drizzle",
    61: "slight rain",
    63: "moderate rain",
    65: "heavy rain",
    66: "light freezing rain",
    67: "heavy freezing rain",
    71: "slight snow",
    73: "moderate snow",
    75: "heavy snow",
    77: "snow grains",
    80: "slight rain showers",
    81: "moderate rain showers",
    82: "violent rain showers",
    85: "slight snow showers",
    86: "heavy snow showers",
    95: "thunderstorm",
    96: "thunderstorm with slight hail",
    99: "thunderstorm with heavy hail",
}


def _c_to_f(celsius: float) -> int:
    return round(celsius * 9 / 5 + 32)


def _describe_code(code: Any) -> str:
    try:
        return WMO_CODES.get(int(code), "unknown conditions")
    except (TypeError, ValueError):
        return "unknown conditions"


# 16-point compass, 22.5° per sector starting at north.
_COMPASS = (
    "N",
    "NNE",
    "NE",
    "ENE",
    "E",
    "ESE",
    "SE",
    "SSE",
    "S",
    "SSW",
    "SW",
    "WSW",
    "W",
    "WNW",
    "NW",
    "NNW",
)


def _describe_direction(degrees: Any) -> str | None:
    """Convert a wind bearing in degrees to a compass point, or None if unusable."""
    try:
        return _COMPASS[round(float(degrees) / 22.5) % 16]
    except (TypeError, ValueError):
        return None


def _geocode(city: str) -> tuple[float, float, str] | None:
    """Resolve a city name to (latitude, longitude, label), or None if unknown."""
    resp = requests.get(
        GEOCODE_URL,
        params={"name": city, "count": 1, "language": "en", "format": "json"},
        timeout=_HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    results = (resp.json() or {}).get("results") or []
    if not results:
        return None
    top = results[0]
    label = ", ".join(
        part
        for part in (top.get("name"), top.get("admin1"), top.get("country"))
        if part
    )
    return top["latitude"], top["longitude"], label or city


# ============ Tools ============


@tool
def get_weather(city: str) -> str:
    """Get the current, real weather conditions for a city.

    Uses the Open-Meteo API (no key required). Unknown or misspelled cities are
    reported as not found rather than returning fabricated conditions.

    Args:
        city: The name of the city to get weather for (e.g., "New York", "London", "Tokyo")

    Returns:
        A string describing the current weather conditions
    """
    try:
        geo = _geocode(city)
    except requests.RequestException as exc:
        return f"Sorry, I couldn't reach the weather service for {city} ({exc})."
    if geo is None:
        return (
            f"I couldn't find a place called '{city}'. "
            "Please check the spelling or try a nearby city."
        )

    lat, lon, label = geo
    try:
        resp = requests.get(
            FORECAST_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "current": (
                    "temperature_2m,weather_code,wind_speed_10m,"
                    "relative_humidity_2m,wind_direction_10m"
                ),
                "timezone": "auto",
            },
            timeout=_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        current = (resp.json() or {}).get("current") or {}
    except requests.RequestException as exc:
        return f"Sorry, I couldn't reach the weather service for {label} ({exc})."

    temp_c = current.get("temperature_2m")
    if temp_c is None:
        return f"Weather data for {label} is currently unavailable."

    desc = _describe_code(current.get("weather_code"))

    # Only report fields the API actually returned - never substitute defaults.
    wind = current.get("wind_speed_10m")
    direction = _describe_direction(current.get("wind_direction_10m"))
    if wind is None:
        wind_str = ""
    elif direction is None:
        wind_str = f", wind {wind} km/h"
    else:
        wind_str = f", wind {wind} km/h from the {direction}"

    humidity = current.get("relative_humidity_2m")
    humidity_str = f", humidity {humidity}%" if humidity is not None else ""

    observed = current.get("time")
    observed_str = f" (observed {observed})" if observed else ""

    return (
        f"The weather in {label} is currently {desc}, "
        f"{temp_c}°C ({_c_to_f(temp_c)}°F){wind_str}{humidity_str}{observed_str}."
    )


@tool
def get_forecast(city: str, days: int = 3) -> str:
    """Get the real multi-day weather forecast for a city.

    Uses the Open-Meteo API (no key required) and honors the requested number of
    days (clamped to Open-Meteo's 1–16 day range).

    Args:
        city: The name of the city
        days: Number of days to forecast (default: 3)

    Returns:
        A string with the weather forecast
    """
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 3
    days = max(1, min(days, _MAX_FORECAST_DAYS))

    try:
        geo = _geocode(city)
    except requests.RequestException as exc:
        return f"Sorry, I couldn't reach the weather service for {city} ({exc})."
    if geo is None:
        return (
            f"I couldn't find a place called '{city}'. "
            "Please check the spelling or try a nearby city."
        )

    lat, lon, label = geo
    try:
        resp = requests.get(
            FORECAST_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "daily": "weather_code,temperature_2m_max,temperature_2m_min",
                "forecast_days": days,
                "timezone": "auto",
            },
            timeout=_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        daily = (resp.json() or {}).get("daily") or {}
    except requests.RequestException as exc:
        return f"Sorry, I couldn't reach the weather service for {label} ({exc})."

    dates = daily.get("time") or []
    if not dates:
        return f"Forecast data for {label} is currently unavailable."

    codes = daily.get("weather_code") or []
    highs = daily.get("temperature_2m_max") or []
    lows = daily.get("temperature_2m_min") or []

    lines = []
    for i, d in enumerate(dates[:days]):
        desc = _describe_code(codes[i] if i < len(codes) else None)
        hi = highs[i] if i < len(highs) else "?"
        lo = lows[i] if i < len(lows) else "?"
        lines.append(f"{d}: {desc}, high {hi}°C, low {lo}°C")

    return (
        f"Weather forecast for {label} over the next {len(lines)} day(s):\n"
        + "\n".join(lines)
    )


# ============ Tool Execution ============

# Tool registry for execution
TOOLS = [get_weather, get_forecast]
TOOL_MAP = {t.name: t for t in TOOLS}


def execute_tools(ai_message: AIMessage) -> list[ToolMessage]:
    """Execute tool calls from an AI message and return tool messages."""
    tool_messages = []

    if not ai_message.tool_calls:
        return tool_messages

    for tool_call in ai_message.tool_calls:
        tool_name = tool_call["name"]
        tool_args = tool_call["args"]
        tool_id = tool_call["id"]

        if tool_name in TOOL_MAP:
            result = TOOL_MAP[tool_name].invoke(tool_args)
            tool_messages.append(ToolMessage(content=str(result), tool_call_id=tool_id))
        else:
            tool_messages.append(
                ToolMessage(content=f"Tool {tool_name} not found", tool_call_id=tool_id)
            )

    return tool_messages


# ============ LLM (lazy) ============

# Cache LLM instances so we build them only on first use, keyed by (model,
# temperature). Building ChatOpenAI validates OPENAI_API_KEY, so we must NOT do
# it at server startup - otherwise the agent can't boot (and can't be
# registered) until a key is provided. Deferring construction lets the server
# start and serve its agent card with no key; only actual requests will fail
# when the key is missing/invalid.
_LLM_CACHE: dict[tuple[str, float], Any] = {}


def _get_llm(model: str, temperature: float):
    key = (model, temperature)
    if key not in _LLM_CACHE:
        _LLM_CACHE[key] = ChatOpenAI(model=model, temperature=temperature).bind_tools(
            TOOLS
        )
    return _LLM_CACHE[key]


# ============ System Prompt ============


def _system_prompt() -> str:
    """Build the system prompt, grounding the model in today's date."""
    return f"""You are a weather assistant. Today's date is {date.today().isoformat()}.

You have access to these tools:
- get_weather: Get current weather for a city
- get_forecast: Get weather forecast for a city

Always call a tool to answer a weather question. Then report ONLY what that
tool returned:
- Never invent, estimate, or add a value the tool did not return. If the tool
  gives no humidity, wind, or observation time, omit those - do not guess.
- Report conditions exactly as worded. If the tool says "fog", say "fog"; never
  substitute a nicer description such as "sunny".
- Keep forecast dates exactly as returned. Never relabel them "Day 1/2/3".
- Converting °C to °F is fine. Inventing any other value is not.
- If a tool reports an error or an unknown city, relay that plainly. Never
  answer from memory instead."""


# ============ Agent Chain ============


class WeatherAgentChain(RunnableSerializable[dict, dict]):
    """
    A simple weather agent chain that handles tool calling.

    This is a LangChain Runnable that:
    1. Takes user input
    2. Calls LLM with tools (LLM built lazily on first request)
    3. Executes tools if needed
    4. Returns final response as dict with "output" key
    """

    model: str = "gpt-4o-mini"
    temperature: float = 0
    max_iterations: int = 5

    class Config:
        arbitrary_types_allowed = True

    @property
    def llm(self):
        return _get_llm(self.model, self.temperature)

    def _run_agent_loop(self, user_input: str, is_async: bool = False) -> str:
        """Run the agent loop and return the response content."""
        # Build messages directly. Do not run user_input through ChatPromptTemplate
        # f-string parsing — Hybro-wrapped payloads may contain curly braces.
        current_messages = [
            SystemMessage(content=_system_prompt()),
            HumanMessage(content=user_input),
        ]

        for _ in range(self.max_iterations):
            response = self.llm.invoke(current_messages)
            current_messages.append(response)

            if not response.tool_calls:
                return response.content

            tool_messages = execute_tools(response)
            current_messages.extend(tool_messages)

        return (
            current_messages[-1].content
            if current_messages
            else "Unable to process request."
        )

    async def _arun_agent_loop(self, user_input: str) -> str:
        """Async run the agent loop and return the response content."""
        current_messages = [
            SystemMessage(content=_system_prompt()),
            HumanMessage(content=user_input),
        ]

        for _ in range(self.max_iterations):
            response = await self.llm.ainvoke(current_messages)
            current_messages.append(response)

            if not response.tool_calls:
                return response.content

            tool_messages = execute_tools(response)
            current_messages.extend(tool_messages)

        return (
            current_messages[-1].content
            if current_messages
            else "Unable to process request."
        )

    def invoke(self, input: dict, config=None) -> dict:
        """Invoke the agent with input. Returns dict with 'output' key."""
        user_input = input.get("input", "")
        content = self._run_agent_loop(user_input)
        return {"output": content}

    async def ainvoke(self, input: dict, config=None) -> dict:
        """Async invoke the agent with input. Returns dict with 'output' key."""
        user_input = input.get("input", "")
        content = await self._arun_agent_loop(user_input)
        return {"output": content}


# ============ Agent Creation ============


def create_weather_agent(
    temperature: float = 0,
    **kwargs,
) -> WeatherAgentChain:
    """
    Create a weather agent with tool calling capabilities (OpenAI).

    Args:
        temperature: Temperature for response generation

    Returns:
        WeatherAgentChain instance ready to invoke
    """
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    # Create and return the agent chain. The LLM itself is built lazily on the
    # first request (see WeatherAgentChain.llm), so the server can start and
    # register without an OpenAI key.
    return WeatherAgentChain(model=model, temperature=temperature)


def run_weather_agent(query: str, agent: WeatherAgentChain = None) -> str:
    """
    Run the weather agent with a query.

    Args:
        query: The user's question about weather
        agent: Optional pre-created agent, creates new one if None

    Returns:
        The agent's response as a string
    """
    if agent is None:
        agent = create_weather_agent()

    result = agent.invoke({"input": query})
    return result.get("output", str(result))


# ============ Direct Execution ============

if __name__ == "__main__":
    print("=== Weather Agent Test (OpenAI) ===\n")

    # Create agent
    agent = create_weather_agent()

    # Test queries
    test_queries = [
        "What's the weather in New York?",
        "Give me the forecast for Tokyo for 5 days",
        "How's the weather in London and Paris?",
    ]

    for query in test_queries:
        print(f"Query: {query}")
        print("-" * 40)
        result = run_weather_agent(query, agent)
        print(f"Response: {result}")
        print("\n" + "=" * 50 + "\n")
