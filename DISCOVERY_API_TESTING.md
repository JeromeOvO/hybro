# Discovery API Testing Guide

This guide shows you how to test the Agent Discovery API.

## Prerequisites

1. **Start the backend server:**
   ```bash
   cd multi-agents-backend
   python -m uvicorn main:app --reload
   ```
   The API will be available at `http://localhost:8000` (or your configured port).

2. **Ensure MongoDB is running** and contains agents with embeddings in Pinecone.

## Step 1: Generate an API Key

First, generate an API key for testing:

```bash
cd multi-agents-backend
python scripts/generate_api_key.py --user-id "test_user_123" --name "Test Key"
```

**Important:** Save the API key that's displayed! It will look like:
```
hybro_abc123xyz456...
```

You can also list existing keys:
```bash
python scripts/generate_api_key.py --list --user-id "test_user_123"
```

## Step 2: Test the API

### Basic Test with curl

**Success Case:**
```bash
curl -X POST "http://localhost:8000/api/v1/discovery/agents" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: hybro_YOUR_API_KEY_HERE" \
  -d '{
    "query": "I need help with data analysis",
    "limit": 5
  }'
```

**Expected Response (200 OK):**
```json
{
  "query": "I need help with data analysis",
  "agents": [
    {
      "agent_card": {
        "name": "Data Analyst Agent",
        "description": "...",
        ...
      },
      "match_score": 0.92
    }
  ],
  "count": 1
}
```

### Test Cases

#### 1. **Missing API Key (401)**
```bash
curl -X POST "http://localhost:8000/api/v1/discovery/agents" \
  -H "Content-Type: application/json" \
  -d '{"query": "test query"}'
```

**Expected Response:**
```json
{
  "detail": {
    "error": "missing_key",
    "message": "X-API-Key header is required"
  }
}
```

#### 2. **Invalid API Key (401)**
```bash
curl -X POST "http://localhost:8000/api/v1/discovery/agents" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: invalid_key_123" \
  -d '{"query": "test query"}'
```

**Expected Response:**
```json
{
  "detail": {
    "error": "invalid_key",
    "message": "Invalid API key"
  }
}
```

#### 3. **No Agents Found (404)**
```bash
curl -X POST "http://localhost:8000/api/v1/discovery/agents" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: hybro_YOUR_API_KEY_HERE" \
  -d '{
    "query": "completely unrelated query that matches nothing",
    "limit": 10
  }'
```

**Expected Response:**
```json
{
  "detail": {
    "error": "no_agent_found",
    "message": "No agent found matching your query with sufficient confidence"
  }
}
```

#### 4. **Missing Query Field (422)**
```bash
curl -X POST "http://localhost:8000/api/v1/discovery/agents" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: hybro_YOUR_API_KEY_HERE" \
  -d '{"limit": 5}'
```

**Expected Response:** Validation error from FastAPI

#### 5. **Invalid Limit (422)**
```bash
curl -X POST "http://localhost:8000/api/v1/discovery/agents" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: hybro_YOUR_API_KEY_HERE" \
  -d '{
    "query": "test",
    "limit": 200
  }
```

**Expected Response:** Validation error (limit must be <= 100)

#### 6. **Default Limit (no limit specified)**
```bash
curl -X POST "http://localhost:8000/api/v1/discovery/agents" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: hybro_YOUR_API_KEY_HERE" \
  -d '{
    "query": "help me with coding"
  }'
```

**Expected Response:** Returns up to 10 agents (default limit)

## Step 3: Test with Python

Create a test script `test_discovery_api.py`:

```python
import requests
import json

API_BASE_URL = "http://localhost:8000/api/v1"
API_KEY = "hybro_YOUR_API_KEY_HERE"  # Replace with your actual key

def test_discovery_api():
    """Test the Discovery API"""
    
    url = f"{API_BASE_URL}/discovery/agents"
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": API_KEY
    }
    
    # Test 1: Basic search
    print("Test 1: Basic search")
    payload = {
        "query": "I need help with data analysis",
        "limit": 5
    }
    response = requests.post(url, headers=headers, json=payload)
    print(f"Status: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2)}")
    print()
    
    # Test 2: Search with default limit
    print("Test 2: Search with default limit")
    payload = {
        "query": "code review assistant"
    }
    response = requests.post(url, headers=headers, json=payload)
    print(f"Status: {response.status_code}")
    print(f"Count: {response.json().get('count', 0)}")
    print()
    
    # Test 3: Invalid API key
    print("Test 3: Invalid API key")
    headers_invalid = headers.copy()
    headers_invalid["X-API-Key"] = "invalid_key"
    response = requests.post(url, headers=headers_invalid, json=payload)
    print(f"Status: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2)}")
    print()
    
    # Test 4: Missing API key
    print("Test 4: Missing API key")
    headers_no_key = {"Content-Type": "application/json"}
    response = requests.post(url, headers=headers_no_key, json=payload)
    print(f"Status: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2)}")

if __name__ == "__main__":
    test_discovery_api()
```

Run it:
```bash
python test_discovery_api.py
```

## Step 4: Test with FastAPI Interactive Docs

FastAPI automatically generates interactive API documentation:

1. **Start the server:**
   ```bash
   python -m uvicorn main:app --reload
   ```

2. **Open in browser:**
   - Swagger UI: `http://localhost:8000/docs`
   - ReDoc: `http://localhost:8000/redoc`

3. **Test the endpoint:**
   - Find `/api/v1/discovery/agents` endpoint
   - Click "Try it out"
   - Click "Authorize" and enter your API key (format: `hybro_...`)
   - Enter request body:
     ```json
     {
       "query": "data analysis",
       "limit": 5
     }
     ```
   - Click "Execute"
   - View the response

## Step 5: Verify API Key Usage Tracking

After making requests, check that usage is being tracked:

```bash
python scripts/generate_api_key.py --list --user-id "test_user_123"
```

You should see:
- `usage_count` incremented
- `last_used_at` updated

## Step 6: Test CORS (Cross-Origin)

If testing from a browser or different origin:

```bash
curl -X OPTIONS "http://localhost:8000/api/v1/discovery/agents" \
  -H "Origin: https://www.google.com/" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: X-API-Key,Content-Type" \
  -v
```

**Expected:** CORS headers should be present in the response.

## Common Issues

### Issue: "Invalid API key"
- **Solution:** Make sure you copied the full API key (starts with `hybro_`)
- **Solution:** Verify the key exists: `python scripts/generate_api_key.py --list --user-id YOUR_USER_ID`

### Issue: "No agent found"
- **Solution:** This is expected if no agents match with score >= 0.5
- **Solution:** Try a more general query or check that agents exist in your database

### Issue: Connection refused
- **Solution:** Make sure the backend server is running on the correct port
- **Solution:** Check `http://localhost:8000/health` to verify server is up

### Issue: MongoDB connection error
- **Solution:** Ensure MongoDB is running and accessible
- **Solution:** Check your MongoDB connection settings in `.env`

## Production Testing

For production, replace `localhost:8000` with your production API URL:

```bash
curl -X POST "https://api.hybro.ai/api/v1/discovery/agents" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: hybro_YOUR_API_KEY_HERE" \
  -d '{
    "query": "I need help with data analysis",
    "limit": 5
  }'
```

## Next Steps

- Monitor API usage in logs
- Set up rate limiting (if needed in the future)
- Add more test cases for edge cases
- Test with different query types and lengths

