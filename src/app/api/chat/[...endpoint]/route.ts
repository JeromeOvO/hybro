import { NextRequest, NextResponse } from 'next/server'

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000'

export async function POST(
  request: NextRequest,
  { params }: { params: { endpoint: string[] } }
) {
  try {
    const body = await request.json()
    const endpoint = (await params).endpoint.join('/')
    
    console.log(`Proxying request to: ${API_BASE_URL}/chat/${endpoint}`)
    
    const response = await fetch(`${API_BASE_URL}/chat/${endpoint}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
    })

    if (!response.ok) {
      const errorText = await response.text()
      console.error(`Backend error for ${endpoint}:`, response.status, errorText)
      return NextResponse.json(
        { error: `Backend error: ${errorText}` },
        { status: response.status }
      )
    }

    const data = await response.json()
    return NextResponse.json(data)
  } catch (error) {
    console.error('Proxy error:', error)
    return NextResponse.json(
      { error: 'Internal server error' },
      { status: 500 }
    )
  }
}

export async function GET(
  request: NextRequest,
  { params }: { params: { endpoint: string[] } }
) {
  try {
    const endpoint = (await params).endpoint.join('/')
    
    console.log(`Proxying GET request to: ${API_BASE_URL}/chat/${endpoint}`)
    
    const response = await fetch(`${API_BASE_URL}/chat/${endpoint}`, {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
      },
    })

    if (!response.ok) {
      const errorText = await response.text()
      console.error(`Backend error for ${endpoint}:`, response.status, errorText)
      return NextResponse.json(
        { error: `Backend error: ${errorText}` },
        { status: response.status }
      )
    }

    const data = await response.json()
    return NextResponse.json(data)
  } catch (error) {
    console.error('Proxy error:', error)
    return NextResponse.json(
      { error: 'Internal server error' },
      { status: 500 }
    )
  }
}