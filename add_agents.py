import requests
import json
import uuid

# API base URL
BASE_URL = "http://localhost:8000"

# Define some sample agents
agents = [
    {
        "id": str(uuid.uuid4()),
        "name": "Market Research Analyst",
        "description": "Analyzes market trends, competition, and customer demographics.",
        "capabilities": ["market analysis", "competitor research", "customer segmentation"],
        "agent_type": "research",
        "model": "gpt-4-turbo",
        "parameters": {
            "temperature": 0.2,
            "max_tokens": 2000
        },
        "prompt_template": "You are a market research expert. Analyze the following information and provide insights: {{input}}"
    },
    {
        "id": str(uuid.uuid4()),
        "name": "Content Creator",
        "description": "Creates engaging marketing content for various platforms.",
        "capabilities": ["copywriting", "content creation", "messaging"],
        "agent_type": "writing",
        "model": "gpt-4-turbo",
        "parameters": {
            "temperature": 0.7,
            "max_tokens": 1500
        },
        "prompt_template": "You are a creative marketing copywriter. Create compelling content for: {{input}}"
    },
    {
        "id": str(uuid.uuid4()),
        "name": "Social Media Strategist",
        "description": "Develops social media strategies and campaign plans.",
        "capabilities": ["social media planning", "campaign strategy", "platform selection"],
        "agent_type": "writing",
        "model": "gpt-4-turbo",
        "parameters": {
            "temperature": 0.5,
            "max_tokens": 2000
        },
        "prompt_template": "You are a social media strategy expert. Create a detailed plan for: {{input}}"
    },
    {
        "id": str(uuid.uuid4()),
        "name": "Budget Planner",
        "description": "Creates cost-effective marketing budgets and ROI projections.",
        "capabilities": ["budget allocation", "cost analysis", "ROI projection"],
        "agent_type": "math",
        "model": "gpt-4-turbo",
        "parameters": {
            "temperature": 0.2,
            "max_tokens": 1500
        },
        "prompt_template": "You are a marketing budget specialist. Create a detailed budget plan for: {{input}}"
    },
    {
        "id": str(uuid.uuid4()),
        "name": "Project Manager",
        "description": "Organizes marketing campaigns into actionable timelines and milestones.",
        "capabilities": ["project planning", "timeline creation", "milestone tracking"],
        "agent_type": "general",
        "model": "gpt-4-turbo",
        "parameters": {
            "temperature": 0.3,
            "max_tokens": 2000
        },
        "prompt_template": "You are a marketing project manager. Create a detailed project plan for: {{input}}"
    }
]

def add_agent(agent_data):
    """Add a single agent to the system"""
    try:
        response = requests.post(f"{BASE_URL}/agents", json=agent_data)
        
        if response.status_code == 200 or response.status_code == 201:
            print(f"✅ Successfully added agent: {agent_data['name']}")
            return response.json()
        else:
            print(f"❌ Failed to add agent {agent_data['name']}: {response.status_code}")
            print(f"   Error: {response.text}")
            return None
    except Exception as e:
        print(f"❌ Exception when adding agent {agent_data['name']}: {str(e)}")
        return None

def main():
    """Add all predefined agents to the system"""
    print("=== Adding Agents to Multi-Agent System ===\n")
    
    successful_agents = []
    
    for agent in agents:
        result = add_agent(agent)
        if result:
            successful_agents.append(result)
    
    print(f"\nAdded {len(successful_agents)} out of {len(agents)} agents")
    
    # Save the successful agents to a file for reference
    with open("added_agents.json", "w") as f:
        json.dump(successful_agents, f, indent=2)
    
    print("\nAgent IDs for reference:")
    for agent in successful_agents:
        print(f"- {agent['name']}: {agent['id']}")

if __name__ == "__main__":
    main() 