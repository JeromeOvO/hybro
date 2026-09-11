import os
import traceback

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from agent_card import build_travel_planner_agent_card
from agent_executor import TravelPlannerAgentExecutor
from google.protobuf.json_format import MessageToJson
from starlette.applications import Starlette

if __name__ == "__main__":
    try:
        host = os.getenv("SERVER_HOST", "0.0.0.0")
        port = int(os.getenv("SERVER_PORT", "7002"))
        server_domain = os.getenv("SERVER_DOMAIN", "localhost")
        agent_host_url = f"http://{server_domain}:{port}/"

        print("Creating AgentCard...")
        agent_card = build_travel_planner_agent_card(agent_host_url)
        print("AgentCard created successfully")
        print("AgentCard JSON:")
        print(MessageToJson(agent_card, indent=2))

        print("Creating DefaultRequestHandler...")
        request_handler = DefaultRequestHandler(
            agent_executor=TravelPlannerAgentExecutor(),
            task_store=InMemoryTaskStore(),
            agent_card=agent_card,
        )
        print("DefaultRequestHandler created successfully")

        print("Building Starlette app...")
        routes = create_agent_card_routes(agent_card) + create_jsonrpc_routes(
            request_handler, rpc_url="/"
        )
        app = Starlette(routes=routes)
        print("Starlette app built successfully")

        print("Routes registered:")
        for route in app.routes:
            print(f"  - {route.methods} {route.path}")

        print("Starting uvicorn server...")
        import uvicorn

        uvicorn.run(app, host=host, port=port)

    except Exception as e:
        print(f"Error occurred: {e}")
        traceback.print_exc()
