# Multi-Agent Task Orchestration System

This README provides instructions for setting up and running the Multi-Agent Task Orchestration System, which allows you to create tasks, decompose them into subtasks, and distribute them to specialized agents.

## Setup Instructions

### 1. Environment Setup

Use the existing virtual environment:

```bash
# Activate the virtual environment
# On Windows
.\venv\Scripts\activate

# On macOS/Linux
source venv/bin/activate
```

### 2. MongoDB Configuration

1. Create a new database in MongoDB according to the name specified in your config file
2. Create the following collections in the database:
   - `agents`
   - `tasks`
   - `child_tasks`

```bash
# Example using MongoDB CLI
mongosh
use your_database_name
db.createCollection("agents")
db.createCollection("tasks")
db.createCollection("child_tasks")
```

### 3. Add Agents to MongoDB

Use the provided script to add agents to the MongoDB database:

```bash
# Navigate to the scripts directory
cd scripts

# Run the add_agents script
python add_agents_scripts.py
```

### 4. Start the A2A Server

We recommend using the Google ADK agent for initial testing:

1. Navigate to the agent's directory:

   ```bash
   cd agents/google_adk
   ```

2. Follow the README instructions to start the A2A server:
   ```bash
   # Example command (may vary based on the agent's README)
   python server.py
   ```

### 5. Start the Main Application

Start the main application using Uvicorn:

```bash
# From the project root directory
uvicorn main:app --reload
```

The API will be available at `http://localhost:8000`.

## Testing the API

You can test the system using the following API endpoints:

### 1. Create a Task

```bash
curl -X POST http://localhost:8000/tasks/create \
  -H "Content-Type: application/json" \
  -d '{"input": "Write a Python script to analyze stock market data"}'
```

This will return a task ID that you can use in subsequent requests.

### 2. Decompose the Task

```bash
curl -X POST http://localhost:8000/tasks/decompose \
  -H "Content-Type: application/json" \
  -d '{"task_id": "your_task_id_here"}'
```

This will break down the task into subtasks and return the subtask IDs.

### 3. Execute a Subtask

```bash
curl -X POST http://localhost:8000/tasks/execute \
  -H "Content-Type: application/json" \
  -d '{"child_task_id": "your_child_task_id_here"}'
```

This will send the subtask to the most appropriate agent and return the result.

### 4. Get Task Status

```bash
curl -X GET http://localhost:8000/tasks/status/your_task_id_here
```

This will return the current status of the task and its subtasks.

## Data model sharing between backend and frontend

Data models defined via Pydantic in the backend here should serve as the single source of truth and are shared with the frontend. Frontend Typescript data models can be generated using pydantic2ts like following example:

``bash
pydantic2ts --module ./models/agent.py --output ./models/agent.ts
``

## Troubleshooting

- If you encounter connection issues with MongoDB, check your database configuration in the config file.
- If agents are not responding, ensure the A2A server is running correctly.
- Check the logs for detailed error messages if any API calls fail.

## Next Steps

- Explore adding more agents to handle specialized tasks
- Customize the task decomposition logic
- Implement more advanced agent selection algorithms

Happy orchestrating!
