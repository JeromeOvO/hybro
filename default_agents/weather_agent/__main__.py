"""
Entry point for `python __main__.py` / `python -m weather_agent`.
Starts the Weather Agent as an A2A server.
"""
try:
    from .a2a_main import main
except ImportError:
    from a2a_main import main

if __name__ == "__main__":
    main()
