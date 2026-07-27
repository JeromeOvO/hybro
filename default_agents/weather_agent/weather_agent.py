"""
Weather Agent using LangChain with Tool Calling (OpenAI)

This module creates a weather agent that can be:
1. Run directly for testing
2. Imported and used by a2a_main.py for A2A exposure

The agent accepts a city name and returns weather information.
"""

import os
from typing import Any

from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableSerializable

load_dotenv()


# ============ Tools ============

@tool
def get_weather(city: str) -> str:
    """Get the current weather for a city.

    Args:
        city: The name of the city to get weather for (e.g., "New York", "London", "Tokyo")

    Returns:
        A string describing the current weather conditions
    """
    # Fake weather data for demo purposes
    # In production, this would call a real weather API
    weather_data = {
        "new york": "sunny, 72°F (22°C)",
        "london": "cloudy, 59°F (15°C)",
        "tokyo": "rainy, 68°F (20°C)",
        "paris": "partly cloudy, 64°F (18°C)",
        "sydney": "clear, 77°F (25°C)",
    }

    city_lower = city.lower()
    if city_lower in weather_data:
        return f"The weather in {city} is currently {weather_data[city_lower]}."
    else:
        return f"The weather in {city} is currently mild, around 65°F (18°C) with clear skies."


@tool
def get_forecast(city: str, days: int = 3) -> str:
    """Get the weather forecast for a city for the next few days.

    Args:
        city: The name of the city
        days: Number of days to forecast (default: 3)

    Returns:
        A string with the weather forecast
    """
    return f"Weather forecast for {city} over the next {days} days: Day 1 - Sunny, Day 2 - Partly cloudy, Day 3 - Clear skies."


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
            tool_messages.append(
                ToolMessage(content=str(result), tool_call_id=tool_id)
            )
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
        _LLM_CACHE[key] = ChatOpenAI(model=model, temperature=temperature).bind_tools(TOOLS)
    return _LLM_CACHE[key]


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
        messages = [
            ("system", """You are a helpful weather assistant. You have access to these tools:
- get_weather: Get current weather for a city
- get_forecast: Get weather forecast for a city

Always use the tools to get accurate weather information. Be friendly and helpful."""),
            ("human", user_input),
        ]

        prompt = ChatPromptTemplate.from_messages(messages)
        formatted = prompt.invoke({})
        current_messages = list(formatted.messages)

        for _ in range(self.max_iterations):
            response = self.llm.invoke(current_messages)
            current_messages.append(response)

            if not response.tool_calls:
                return response.content

            tool_messages = execute_tools(response)
            current_messages.extend(tool_messages)

        return current_messages[-1].content if current_messages else "Unable to process request."

    async def _arun_agent_loop(self, user_input: str) -> str:
        """Async run the agent loop and return the response content."""
        messages = [
            ("system", """You are a helpful weather assistant. You have access to these tools:
- get_weather: Get current weather for a city
- get_forecast: Get weather forecast for a city

Always use the tools to get accurate weather information. Be friendly and helpful."""),
            ("human", user_input),
        ]

        prompt = ChatPromptTemplate.from_messages(messages)
        formatted = prompt.invoke({})
        current_messages = list(formatted.messages)

        for _ in range(self.max_iterations):
            response = await self.llm.ainvoke(current_messages)
            current_messages.append(response)

            if not response.tool_calls:
                return response.content

            tool_messages = execute_tools(response)
            current_messages.extend(tool_messages)

        return current_messages[-1].content if current_messages else "Unable to process request."

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
