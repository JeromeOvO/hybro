import requests
import time
import json
import datetime
from colorama import Fore, Style, init
from tabulate import tabulate

# Initialize colorama
init()

# API base URL
BASE_URL = "http://localhost:8000"

def create_task(user_input=None):
    """Create a new task and return the task_id"""
    # Default input if none provided
    if not user_input:
        user_input = "I need a comprehensive marketing plan for my new fitness app called 'FitTrack'. The app targets busy professionals ages 25-40. Please include social media strategy, pricing recommendations, and a 3-month launch timeline."
    
    task_data = {
        "query": "Create a detailed marketing strategy based on the user's requirements.",
        "user_input": user_input,
        "deadline": "2023-12-31T23:59:59Z",
        "priority": "high"
    }
    
    response = requests.post(f"{BASE_URL}/tasks", json=task_data)
    
    if response.status_code != 200 and response.status_code != 201:
        print(f"Failed to create task: {response.status_code}")
        print(response.json())
        return None
        
    result = response.json()
    print(f"{Fore.GREEN}Task created successfully with ID: {result['task_id']}{Style.RESET_ALL}")
    return result['task_id']

def get_task_details(task_id):
    """Get detailed information about a task"""
    response = requests.get(f"{BASE_URL}/tasks/{task_id}")
    
    if response.status_code != 200:
        print(f"Error getting task details: {response.status_code}")
        print(response.json())
        return None
        
    return response.json()

def get_agent_details(agent_id):
    """Get detailed information about an agent"""
    if not agent_id:
        return {"name": "Unassigned", "agent_type": "None"}
        
    response = requests.get(f"{BASE_URL}/agents/{agent_id}")
    
    if response.status_code != 200:
        print(f"Error getting agent details for agent ID {agent_id}: {response.status_code}")
        return {"name": f"Unknown (ID: {agent_id})", "agent_type": "Unknown"}
        
    return response.json()

def display_step_summary(steps):
    """Display a summary of all steps in a table format"""
    print(f"\n{Fore.CYAN}Step Summary:{Style.RESET_ALL}")
    
    table_data = []
    for i, step in enumerate(steps):
        # Get status with color
        status = step.get('status', 'PENDING')
        status_color = Fore.YELLOW
        if status == 'COMPLETED':
            status_color = Fore.GREEN
        elif status == 'FAILED':
            status_color = Fore.RED
        colored_status = f"{status_color}{status}{Style.RESET_ALL}"
        
        # Get agent name
        agent_name = "Unassigned"
        agent_id = step.get('agent_id')
        if agent_id:
            agent_details = get_agent_details(agent_id)
            agent_name = agent_details.get('name', f"Unknown (ID: {agent_id})")
        
        # Add row to table
        description = step.get('description', 'No description')
        if len(description) > 45:
            description = description[:45] + "..."
            
        table_data.append([
            i+1, 
            description, 
            agent_name,
            colored_status
        ])
    
    # Display the table
    print(tabulate(
        table_data, 
        headers=["Step", "Description", "Agent", "Status"],
        tablefmt="grid"
    ))

def display_step_details(step, step_num):
    """Display detailed information about a step"""
    print(f"\n{Fore.CYAN}{'='*80}")
    print(f"STEP {step_num}: {step.get('description', 'No description')}")
    print(f"{'='*80}{Style.RESET_ALL}")
    
    # Status with color
    status = step.get('status', 'UNKNOWN')
    status_color = Fore.YELLOW
    if status == 'COMPLETED':
        status_color = Fore.GREEN
    elif status == 'FAILED':
        status_color = Fore.RED
    
    print(f"Status: {status_color}{status}{Style.RESET_ALL}")
    
    # Get agent details if assigned
    agent_id = step.get('agent_id')
    if agent_id:
        agent_details = get_agent_details(agent_id)
        print(f"Agent: {Fore.MAGENTA}{agent_details.get('name', f'Unknown (ID: {agent_id})')}{Style.RESET_ALL} (Type: {agent_details.get('agent_type', 'Unknown')})")
    else:
        print(f"Agent: Not assigned")
    
    # Input/Output - check all possible field names
    print(f"\n{Fore.BLUE}Input:{Style.RESET_ALL}")
    input_content = None
    # Try different possible field names for input
    for field in ['input_data', 'input', 'context']:
        if step.get(field) and step[field] not in [None, ""]:
            input_content = step[field]
            break
    
    if input_content:
        try:
            if isinstance(input_content, str):
                # Try to parse as JSON
                try:
                    input_json = json.loads(input_content)
                    print(json.dumps(input_json, indent=2))
                except:
                    # Not JSON, print as is
                    print(input_content)
            else:
                # Already a dict or other structure
                print(json.dumps(input_content, indent=2))
        except:
            print(str(input_content))
    else:
        print("No input data")
    
    print(f"\n{Fore.GREEN}Output:{Style.RESET_ALL}")
    output_content = None
    # Try different possible field names for output
    for field in ['output_data', 'output', 'result']:
        if step.get(field) and step[field] not in [None, ""]:
            output_content = step[field]
            break
    
    if output_content:
        try:
            if isinstance(output_content, str):
                # Try to parse as JSON
                try:
                    output_json = json.loads(output_content)
                    print(json.dumps(output_json, indent=2))
                except:
                    # Not JSON, print as is
                    print(output_content)
            else:
                # Already a dict or other structure
                print(json.dumps(output_content, indent=2))
        except:
            print(str(output_content))
    else:
        print("No output data")
    
    # Error if any
    if step.get('error'):
        print(f"\n{Fore.RED}Error:{Style.RESET_ALL}")
        print(step['error'])

def display_final_result(task_data):
    """Display the final result of the task"""
    print(f"\n{Fore.GREEN}{'='*80}")
    print(f"FINAL RESULT")
    print(f"{'='*80}{Style.RESET_ALL}")
    
    if task_data.get('result'):
        try:
            # Try to parse as JSON for pretty printing
            if isinstance(task_data['result'], str):
                result_json = json.loads(task_data['result'])
                print(json.dumps(result_json, indent=2))
            else:
                print(json.dumps(task_data['result'], indent=2))
        except:
            # If not JSON, print as is
            print(task_data.get('result', 'None'))
    else:
        print("No final result available")
    
    # Show task status
    status = task_data.get('status', 'UNKNOWN')
    status_color = Fore.YELLOW
    if status == 'COMPLETED':
        status_color = Fore.GREEN
    elif status == 'FAILED':
        status_color = Fore.RED
    
    print(f"\nTask status: {status_color}{status}{Style.RESET_ALL}")
    
    # Show error if task failed
    if status == 'FAILED':
        print(f"\n{Fore.RED}Error:{Style.RESET_ALL}")
        print(task_data.get('error', 'No error information available'))

def monitor_task(task_id, poll_interval=5, max_polls=60):
    """Monitor a task, show step updates, and display the final result"""
    print(f"\n{Fore.YELLOW}Monitoring task {task_id}...{Style.RESET_ALL}")
    
    previous_steps = []
    poll_count = 0
    task_completed = False
    
    while poll_count < max_polls and not task_completed:
        poll_count += 1
        task_data = get_task_details(task_id)
        
        if not task_data:
            print("Failed to get task details. Retrying...")
            time.sleep(poll_interval)
            continue
        
        steps = task_data.get('steps', [])
        
        # Display step summary
        status = task_data.get('status', 'unknown')
        print(f"\n{Fore.CYAN}Poll {poll_count}: Task status is '{status}'{Style.RESET_ALL}")
        display_step_summary(steps)
        
        # Check for new or updated steps
        for i, step in enumerate(steps):
            # New step or updated status
            is_new_step = i >= len(previous_steps)
            is_updated_step = not is_new_step and (
                step.get('status') != previous_steps[i].get('status') or
                step.get('result') != previous_steps[i].get('result')
            )
            
            if is_new_step or is_updated_step:
                display_step_details(step, i+1)
        
        # Update previous steps
        previous_steps = steps.copy()
        
        # Check if all steps are completed
        if steps and all(step.get('status') in ['COMPLETED', 'FAILED'] for step in steps):
            print(f"\n{Fore.GREEN}All steps have been processed. Getting final result...{Style.RESET_ALL}")
            
            # Wait a moment for the final result to be updated
            time.sleep(2)
            
            # Get the most up-to-date task data for the final result
            final_task_data = get_task_details(task_id)
            
            # Display final result
            display_final_result(final_task_data)
            task_completed = True
            return final_task_data
        
        # Also check if task is marked as completed
        if status in ['completed', 'failed']:
            print(f"\n{Fore.GREEN}Task marked as {status}. Getting final result...{Style.RESET_ALL}")
            
            # Get the most up-to-date task data
            final_task_data = get_task_details(task_id)
            
            # Display final result
            display_final_result(final_task_data)
            task_completed = True
            return final_task_data
        
        time.sleep(poll_interval)
    
    # If we got here, the task didn't complete within the time limit
    if not task_completed:
        print(f"\n{Fore.RED}Maximum polling attempts reached. Task not completed.{Style.RESET_ALL}")
        # Get one last update and show the current state
        final_data = get_task_details(task_id)
        display_final_result(final_data)
        return final_data

def main():
    # Get user input
    print(f"{Fore.CYAN}Enter your query (or press Enter for default):{Style.RESET_ALL}")
    user_input = input().strip()
    
    # Create a task with user input
    task_id = create_task(user_input if user_input else None)
    if not task_id:
        return
    
    # Monitor the task and display details
    monitor_task(task_id)

if __name__ == "__main__":
    main() 