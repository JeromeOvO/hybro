import { NextRequest } from 'next/server'

// Set longer timeout duration (maximum 5 minutes for SSE streams)
export const maxDuration = 300

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000'
const API_PREFIX = process.env.NEXT_PUBLIC_API_PREFIX || '/api/v1'

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ endpoint: string[] }> }
) {
  try {
    const body = await request.json()
    const endpoint = (await params).endpoint.join('/')
    
    console.log(`Proxying SSE POST request to: ${API_BASE_URL}${API_PREFIX}/sse/${endpoint}`)
    
    // Add fetch timeout
    const controller = new AbortController()
    const timeoutId = setTimeout(() => controller.abort(), 300000) // 5 minutes timeout
    
    const response = await fetch(`${API_BASE_URL}${API_PREFIX}/sse/${endpoint}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
      signal: controller.signal
    })

    clearTimeout(timeoutId)

    if (!response.ok) {
      const errorText = await response.text()
      console.error(`Backend error for SSE ${endpoint}:`, response.status, errorText)
      return new Response(
        JSON.stringify({ error: `Backend error: ${errorText}` }),
        { 
          status: response.status,
          headers: { 'Content-Type': 'application/json' }
        }
      )
    }

    const data = await response.json()
    return new Response(JSON.stringify(data), {
      headers: { 'Content-Type': 'application/json' }
    })
  } catch (error) {
    console.error('SSE Proxy error:', error)
    if (error instanceof Error && error.name === 'AbortError') {
      return new Response(
        JSON.stringify({ error: 'Request timeout' }),
        { 
          status: 504,
          headers: { 'Content-Type': 'application/json' }
        }
      )
    }
    return new Response(
      JSON.stringify({ error: 'Internal server error' }),
      { 
        status: 500,
        headers: { 'Content-Type': 'application/json' }
      }
    )
  }
}

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ endpoint: string[] }> }
) {
  try {
    const endpoint = (await params).endpoint.join('/')
    
    console.log(`Proxying SSE GET request to: ${API_BASE_URL}${API_PREFIX}/sse/${endpoint}`)
    
    // Check if this is a streaming endpoint (like /room/{room_id}/stream)
    if (endpoint.includes('/stream')) {
      return handleSSEStream(endpoint, request)
    }
    
    // Handle regular GET requests (like status endpoints)
    const response = await fetch(`${API_BASE_URL}${API_PREFIX}/sse/${endpoint}`, {
      method: 'GET',
      headers: {
        'Accept': 'application/json',
      },
    })

    if (!response.ok) {
      const errorText = await response.text()
      console.error(`Backend error for SSE ${endpoint}:`, response.status, errorText)
      return new Response(
        JSON.stringify({ error: `Backend error: ${errorText}` }),
        { 
          status: response.status,
          headers: { 'Content-Type': 'application/json' }
        }
      )
    }

    const data = await response.json()
    return new Response(JSON.stringify(data), {
      headers: { 'Content-Type': 'application/json' }
    })
  } catch (error) {
    console.error('SSE Proxy error:', error)
    return new Response(
      JSON.stringify({ error: 'Internal server error' }),
      { 
        status: 500,
        headers: { 'Content-Type': 'application/json' }
      }
    )
  }
}

// Handle SSE streaming responses
async function handleSSEStream(endpoint: string, request: NextRequest) {
  const controller = new AbortController()
  
  // Set up cleanup on request abort
  request.signal.addEventListener('abort', () => {
    console.log('SSE request aborted by client')
    controller.abort()
  })

  try {
    console.log(`Starting SSE stream to: ${API_BASE_URL}${API_PREFIX}/sse/${endpoint}`)
    
    const response = await fetch(`${API_BASE_URL}${API_PREFIX}/sse/${endpoint}`, {
      method: 'GET',
      headers: {
        'Accept': 'text/event-stream',
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
      },
      signal: controller.signal
    })

    if (!response.ok) {
      const errorText = await response.text()
      console.error(`SSE Backend error for ${endpoint}:`, response.status, errorText)
      return new Response(
        `data: ${JSON.stringify({ type: 'error', error: `Backend error: ${errorText}` })}\n\n`,
        {
          status: 200,
          headers: {
            'Content-Type': 'text/event-stream',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Cache-Control, Content-Type',
            'Access-Control-Allow-Methods': 'GET, OPTIONS'
          }
        }
      )
    }

    // Create a readable stream to proxy the SSE data
    const readable = new ReadableStream({
      async start(controller) {
        const reader = response.body?.getReader()
        const decoder = new TextDecoder()

        if (!reader) {
          controller.error(new Error('No response body'))
          return
        }

        try {
          while (true) {
            const { done, value } = await reader.read()
            
            if (done) {
              console.log('SSE stream ended')
              break
            }

            // Decode and forward the chunk
            const chunk = decoder.decode(value, { stream: true })
            controller.enqueue(new TextEncoder().encode(chunk))
          }
        } catch (error) {
          console.error('SSE stream error:', error)
          controller.error(error)
        } finally {
          controller.close()
        }
      },
      
      cancel() {
        console.log('SSE stream cancelled')
        controller.abort()
      }
    })

    return new Response(readable, {
      headers: {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': 'Cache-Control, Content-Type',
        'Access-Control-Allow-Methods': 'GET, OPTIONS'
      }
    })

  } catch (error) {
    console.error('SSE stream setup error:', error)
    
    if (error instanceof Error && error.name === 'AbortError') {
      return new Response(
        `data: ${JSON.stringify({ type: 'error', error: 'Connection aborted' })}\n\n`,
        {
          status: 200,
          headers: {
            'Content-Type': 'text/event-stream',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive'
          }
        }
      )
    }

    return new Response(
      `data: ${JSON.stringify({ type: 'error', error: 'Stream setup failed' })}\n\n`,
      {
        status: 200,
        headers: {
          'Content-Type': 'text/event-stream',
          'Cache-Control': 'no-cache',
          'Connection': 'keep-alive'
        }
      }
    )
  }
}

// Handle OPTIONS for CORS
export async function OPTIONS() {
  return new Response(null, {
    status: 200,
    headers: {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, Cache-Control',
    }
  })
}