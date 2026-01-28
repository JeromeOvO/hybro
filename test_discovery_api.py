#!/usr/bin/env python3
"""
Quick test script for the Discovery API

Usage:
    python test_discovery_api.py
"""

import json
import sys
from pathlib import Path

try:
    import requests
except ImportError:
    print("Error: 'requests' library not found. Install it with: pip install requests")
    sys.exit(1)

# Configuration
API_BASE_URL = "http://localhost:8000/api/v1"
API_KEY = None  # Will prompt if not set


def print_section(title: str):
    """Print a formatted section header"""
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


def test_discovery_api(api_key: str):
    """Test the Discovery API with various scenarios"""
    
    url = f"{API_BASE_URL}/discovery/agents"
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": api_key
    }
    
    # Test 1: Basic search
    print_section("Test 1: Basic Search")
    payload = {
        "query": "I need help with math problems",
        "limit": 5
    }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"Query: {data.get('query')}")
            print(f"Agents Found: {data.get('count', 0)}")
            if data.get('agents'):
                print(f"\nTop Result:")
                top = data['agents'][0]
                print(f"  Match Score: {top.get('match_score', 0):.3f}")
                agent_card = top.get('agent_card', {})
                print(f"  Agent Name: {agent_card.get('name', 'N/A')}")
                print(f"  Description: {agent_card.get('description', 'N/A')[:100]}...")
        else:
            print(f"Error: {json.dumps(response.json(), indent=2)}")
    except requests.exceptions.RequestException as e:
        print(f"Request failed: {e}")
    
    # Test 2: Search with default limit
    print_section("Test 2: Search with Default Limit")
    payload = {
        "query": "code review assistant"
    }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"Agents Found: {data.get('count', 0)}")
            print(f"Note: Default limit is 5 (from settings)")
        else:
            print(f"Error: {json.dumps(response.json(), indent=2)}")
    except requests.exceptions.RequestException as e:
        print(f"Request failed: {e}")
    
    # Test 3: Invalid API key
    print_section("Test 3: Invalid API Key")
    headers_invalid = headers.copy()
    headers_invalid["X-API-Key"] = "invalid_key_12345"
    payload = {"query": "test"}
    try:
        response = requests.post(url, headers=headers_invalid, json=payload, timeout=10)
        print(f"Status Code: {response.status_code}")
        print(f"Expected: 401 Unauthorized")
        if response.status_code == 401:
            print("✓ Correctly rejected invalid API key")
            print(f"Error: {json.dumps(response.json(), indent=2)}")
        else:
            print(f"✗ Unexpected status code")
    except requests.exceptions.RequestException as e:
        print(f"Request failed: {e}")
    
    # Test 4: Missing API key
    print_section("Test 4: Missing API Key")
    headers_no_key = {"Content-Type": "application/json"}
    payload = {"query": "test"}
    try:
        response = requests.post(url, headers=headers_no_key, json=payload, timeout=10)
        print(f"Status Code: {response.status_code}")
        print(f"Expected: 401 Unauthorized")
        if response.status_code == 401:
            print("✓ Correctly rejected missing API key")
            print(f"Error: {json.dumps(response.json(), indent=2)}")
        else:
            print(f"✗ Unexpected status code")
    except requests.exceptions.RequestException as e:
        print(f"Request failed: {e}")
    
    # Test 5: No results (query that likely won't match)
    print_section("Test 5: Query with No Results")
    payload = {
        "query": "completely unrelated query xyz123 that matches nothing",
        "limit": 4
    }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        print(f"Status Code: {response.status_code}")
        if response.status_code == 404:
            print("✓ Correctly returned 404 for no matches")
            print(f"Error: {json.dumps(response.json(), indent=2)}")
        elif response.status_code == 200:
            data = response.json()
            print(f"Agents Found: {data.get('count', 0)}")
            if data.get('count') == 0:
                print("Note: No agents found, but API returned 200 (unexpected) ")
        else:
            print(f"Unexpected status: {json.dumps(response.json(), indent=2)}")
    except requests.exceptions.RequestException as e:
        print(f"Request failed: {e}")
    
    # Test 6: Invalid limit (too high)
    print_section("Test 6: Invalid Limit (Too High)")
    payload = {
        "query": "test",
        "limit": 200  # Max is 100
    }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        print(f"Status Code: {response.status_code}")
        print(f"Expected: 422 Validation Error")
        if response.status_code == 422:
            print("✓ Correctly rejected invalid limit")
            print(f"Error: {json.dumps(response.json(), indent=2)}")
        else:
            print(f"Unexpected status code")
    except requests.exceptions.RequestException as e:
        print(f"Request failed: {e}")


def main():
    """Main function"""
    print("Discovery API Test Script")
    print("=" * 60)
    
    # Get API key
    api_key = API_KEY
    if not api_key:
        print("\nEnter your API key (or press Enter to skip tests requiring auth):")
        api_key = input("API Key: ").strip()
        if not api_key:
            print("\n⚠️  No API key provided. Some tests will be skipped.")
            print("Generate a key with: python scripts/generate_api_key.py")
            return
    
    # Check if server is running
    print("\nChecking if server is running...")
    try:
        health_response = requests.get(f"{API_BASE_URL.replace('/api/v1', '')}/health", timeout=5)
        if health_response.status_code == 200:
            print("✓ Server is running")
        else:
            print(f"⚠️  Server returned status {health_response.status_code}")
    except requests.exceptions.RequestException as e:
        print(f"✗ Cannot connect to server at {API_BASE_URL}")
        print(f"  Error: {e}")
        print("\nMake sure the server is running:")
        print("  python -m uvicorn main:app --reload")
        return
    
    # Run tests
    if api_key:
        test_discovery_api(api_key)
    else:
        print("\nSkipping tests that require API key...")
    
    print_section("Tests Complete")
    print("\nFor more detailed testing, see DISCOVERY_API_TESTING.md")


if __name__ == "__main__":
    main()

