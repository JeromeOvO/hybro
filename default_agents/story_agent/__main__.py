import os
import traceback

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from agent_executor import StoryAgentExecutor


if __name__ == '__main__':
    try:
        host = os.getenv('SERVER_HOST', '0.0.0.0')
        port = int(os.getenv('SERVER_PORT', '7003'))
        server_domain = os.getenv('SERVER_DOMAIN', 'localhost')
        agent_host_url = f'http://{server_domain}:{port}'

        print('Creating AgentSkill...')
        skill = AgentSkill(
            id='story_agent',
            name='story agent',
            description='Tells and writes engaging storis',
            tags=['story'],
            examples=[
                'tell me a story about a cat',
                'tell me a story about a dog',
            ],
        )
        print('✓ AgentSkill created successfully')

        print('Creating AgentCard...')
        agent_card = AgentCard(
            name='Story Agent',
            description='story',
            url=agent_host_url,
            version='1.0.0',
            defaultInputModes=['text'],
            defaultOutputModes=['text'],
            capabilities=AgentCapabilities(streaming=True),
            skills=[skill],
        )
        print('✓ AgentCard created successfully')
        print('AgentCard JSON:')
        print(agent_card.model_dump_json(indent=2))

        print('Creating DefaultRequestHandler...')
        request_handler = DefaultRequestHandler(
            agent_executor=StoryAgentExecutor(),
            task_store=InMemoryTaskStore(),
        )
        print('✓ DefaultRequestHandler created successfully')

        print('Creating A2AStarletteApplication...')
        server = A2AStarletteApplication(
            agent_card=agent_card, http_handler=request_handler
        )
        print('✓ A2AStarletteApplication created successfully')

        print('Building Starlette app...')
        app = server.build()
        print('✓ Starlette app built successfully')

        print('Routes registered:')
        for route in app.routes:
            print(f'  - {route.methods} {route.path}')

        print('Starting uvicorn server...')
        import uvicorn

        uvicorn.run(app, host=host, port=port)

    except Exception as e:
        print(f'❌ Error occurred: {e}')
        traceback.print_exc()
