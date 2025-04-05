import json
from typing import Dict, Any, List
from models.response import Step
from services.openai_service import openai_service
from services.agent_service import agent_service

class Classifier:
    async def classify_step(self, step: Step) -> Step:
        """Classify a step and assign the best agent for it"""
        print(f"Classifying step: {step.step_id} - {step.description[:50]}...")
        
        # Use Classifier AI to identify required capabilities
        capabilities_json = await openai_service.classifier_ai_completion(step.description)
        
        # Parse capabilities
        capabilities_data = json.loads(capabilities_json) if isinstance(capabilities_json, str) else capabilities_json
        capabilities = capabilities_data.get("capabilities", [])
        
        print(f"Identified capabilities: {capabilities}")
        
        # Find the best agent for these capabilities
        if capabilities:
            agents = await agent_service.find_best_agent(capabilities)
            if agents:
                # Assign the best agent to the step
                step.agent_id = agents[0].id
                print(f"Assigned agent {agents[0].id} ({agents[0].name}) to step {step.step_id}")
            else:
                print(f"WARNING: No suitable agent found for capabilities: {capabilities}")
        else:
            print(f"WARNING: No capabilities identified for step: {step.description[:50]}...")
        
        return step

classifier = Classifier() 