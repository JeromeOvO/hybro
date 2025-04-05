import requests

# Test health endpoint
response = requests.get("http://localhost:8000/health")
print(f"Health check: {response.json()}")

# Test creating a task
task_data = {
    "query": "Create a marketing plan for a new mobile app. Please respond with JSON.",
    "user_input": "Create a marketing plan for a new mobile app. Please format your response as JSON.",
    "deadline": "2023-12-31T23:59:59Z",
    "priority": "high"
}
response = requests.post("http://localhost:8000/tasks", json=task_data)
print(f"Create task: {response.json()}")

# Add error handling
if response.status_code == 200 or response.status_code == 201:
    task_id = response.json()["task_id"]
    
    # Test getting task status
    response = requests.get(f"http://localhost:8000/tasks/{task_id}")
    print(f"Task status: {response.json()}")
else:
    print(f"Failed to create task: {response.status_code}")
    print(response.json())