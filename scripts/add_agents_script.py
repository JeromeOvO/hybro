import requests
import uuid
from typing import Dict, Any, List, Optional

# API endpoint configuration
API_URL = "http://localhost:8000"  # Change to your API address
CREATE_AGENT_ENDPOINT = f"{API_URL}/agents/createAgent"

# Predefined agent definitions based on framework implementations
framework_agents = {
    "crewai": {
        "name": "CrewAI Image Generator",
        "description": "An image generation agent built on CrewAI that can create or modify images based on text descriptions",
        "url": "http://localhost:10001",
        "provider": {
            "organization": "CrewAI Integration",
            "url": "https://crewai.io"
        },
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "stateTransitionHistory": True
        },
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text", "image"],
        "skills": [
            {
                "id": "image-generation",
                "name": "Image Generation",
                "description": "Generate high-quality images from text descriptions",
                "tags": ["image", "generation", "creative"]
            },
            {
                "id": "image-modification",
                "name": "Image Modification",
                "description": "Modify existing images based on user instructions",
                "tags": ["image", "editing", "modification"]
            }
        ]
    },
    "google_adk": {
        "name": "Google ADK Reimbursement Assistant",
        "description": "A reimbursement processing agent built with Google ADK, handling employee expense requests and form submissions",
        "url": "http://localhost:10002",
        "provider": {
            "organization": "Google ADK Integration",
            "url": "https://developers.generativeai.google"
        },
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "stateTransitionHistory": True
        },
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text", "form"],
        "skills": [
            {
                "id": "reimbursement-processing",
                "name": "Reimbursement Processing",
                "description": "Process employee reimbursement requests, validate and approve valid expenses",
                "tags": ["reimbursement", "forms", "finance"]
            },
            {
                "id": "form-creation",
                "name": "Form Creation",
                "description": "Create and process structured form data",
                "tags": ["forms", "data processing", "structured"]
            }
        ]
    },
    "langgraph": {
        "name": "LangGraph Currency Assistant",
        "description": "A currency conversion agent built with LangGraph, capable of querying and calculating exchange rates between different currencies",
        "url": "http://localhost:10000",
        "provider": {
            "organization": "LangGraph Integration",
            "url": "https://github.com/langchain-ai/langgraph"
        },
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "stateTransitionHistory": True
        },
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
        "skills": [
            {
                "id": "currency-conversion",
                "name": "Currency Conversion",
                "description": "Convert amounts between different currencies using real-time exchange rates",
                "tags": ["currency", "exchange rates", "conversion"]
            },
            {
                "id": "exchange-rate-lookup",
                "name": "Exchange Rate Lookup",
                "description": "Look up historical exchange rates for specific dates",
                "tags": ["exchange rates", "lookup", "historical data"]
            }
        ]
    },
    # "llama_index_file_chat": {
    #     "name": "LlamaIndex Document Assistant",
    #     "description": "A document Q&A agent built with LlamaIndex, supporting PDF parsing and document content queries",
    #     "url": "http://localhost:10010",
    #     "provider": {
    #         "organization": "LlamaIndex Integration",
    #         "url": "https://www.llamaindex.ai"
    #     },
    #     "capabilities": {
    #         "streaming": True,
    #         "pushNotifications": False,
    #         "stateTransitionHistory": True
    #     },
    #     "defaultInputModes": ["text", "file"],
    #     "defaultOutputModes": ["text", "markdown"],
    #     "skills": [
    #         {
    #             "id": "document-qa",
    #             "name": "Document Q&A",
    #             "description": "Parse documents and answer questions about their content",
    #             "tags": ["document", "QA", "PDF"]
    #         },
    #         {
    #             "id": "citation-generation",
    #             "name": "Citation Generation",
    #             "description": "Extract information from documents and generate citations",
    #             "tags": ["citations", "extraction", "document analysis"]
    #         }
    #     ]
    # },
    "marvin": {
        "name": "Marvin Data Extraction Assistant",
        "description": "A data extraction agent built with Marvin framework, capable of extracting structured information from unstructured text",
        "url": "http://localhost:10030",
        "provider": {
            "organization": "Marvin Integration",
            "url": "https://www.askmarvin.ai"
        },
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "stateTransitionHistory": True
        },
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text", "json"],
        "skills": [
            {
                "id": "information-extraction",
                "name": "Information Extraction",
                "description": "Extract structured data from unstructured text",
                "tags": ["extraction", "structured", "data"]
            },
            {
                "id": "contact-extraction",
                "name": "Contact Extraction",
                "description": "Extract contact information from text",
                "tags": ["contacts", "extraction", "personal info"]
            }
        ]
    },
    "semantickernel": {
        "name": "Semantic Kernel Travel Assistant",
        "description": "A travel planning agent developed with Semantic Kernel, providing travel recommendations, currency conversion, and more",
        "url": "http://localhost:10020",
        "provider": {
            "organization": "Semantic Kernel Integration",
            "url": "https://learn.microsoft.com/en-us/semantic-kernel"
        },
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "stateTransitionHistory": True
        },
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
        "skills": [
            {
                "id": "travel-planning",
                "name": "Travel Planning",
                "description": "Help users plan travel itineraries and activities",
                "tags": ["travel", "planning", "activities"]
            },
            {
                "id": "currency-advice",
                "name": "Currency Advice",
                "description": "Provide currency conversion and financial advice for travel destinations",
                "tags": ["currency", "conversion", "travel finance"]
            }
        ]
    }
}

# Add this function to ensure all agent data is properly formatted
def sanitize_agent_data(agent_data: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure agent data meets expected schema requirements"""
    # Make sure version exists
    if "version" not in agent_data:
        agent_data["version"] = "1.0"
    
    # The safest approach - explicitly set values field as an empty list of floats
    # This ensures the field exists and has the correct type
    agent_data["values"] = []
    
    # Recursively check for 'values' in nested dictionaries
    def fix_nested_values(obj):
        if isinstance(obj, dict):
            if "values" in obj and not isinstance(obj["values"], list):
                obj["values"] = []
            for key, value in obj.items():
                fix_nested_values(value)
        elif isinstance(obj, list):
            for item in obj:
                fix_nested_values(item)
    
    # Apply the recursive fix to the entire agent data
    fix_nested_values(agent_data)
    
    return agent_data

def create_agent(agent_card: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Call API to create an agent"""
    # Generate a unique agent_id for each agent
    agent_id = str(uuid.uuid4())
    
    # Create a payload strictly following the Agent model structure
    payload = {
        "agent_id": agent_id,
        "agentCard": {
            "name": agent_card.get("name", ""),
            "description": agent_card.get("description", ""),
            "url": agent_card.get("url", ""),
            "provider": agent_card.get("provider", {
                "organization": "Default Organization",
                "url": "https://example.com"
            }),
            "version": "1.0",  # Explicitly set version
            "capabilities": agent_card.get("capabilities", {
                "streaming": True,
                "pushNotifications": False,
                "stateTransitionHistory": False
            }),
            "defaultInputModes": agent_card.get("defaultInputModes", ["text"]),
            "defaultOutputModes": agent_card.get("defaultOutputModes", ["text"]),
            "skills": agent_card.get("skills", [])
        },
        "is_remote": True,  # From Agent model default
        "ragUrl": None       # From Agent model default
    }
    
    try:
        response = requests.post(CREATE_AGENT_ENDPOINT, json=payload)
        if response.status_code in (200, 201):
            print(f"✅ Successfully created agent: {agent_card['name']}")
            return response.json()
        else:
            print(f"❌ Failed to create agent {agent_card['name']}: {response.status_code} - {response.text}")
            # Print more debugging information
            print(f"Payload: {payload}")
            return None
    except Exception as e:
        print(f"❌ API request exception: {str(e)}")
        return None

def main():
    """Main function: Create predefined agents for each framework"""
    print("🚀 Starting import of predefined framework agents...")
    
    results = []
    for framework_name, agent_data in framework_agents.items():
        print(f"\nProcessing framework: {framework_name}")
        result = create_agent(agent_data)
        if result:
            results.append(result)
    
    print(f"\n🎉 Successfully imported {len(results)}/{len(framework_agents)} agents!")
    
    if len(results) != len(framework_agents):
        print("⚠️ Some agents failed to import, please check error messages")

if __name__ == "__main__":
    main()