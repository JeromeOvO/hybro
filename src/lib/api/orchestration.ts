// Orchestration-related API functions
import type { OrchestrationCenterResponse } from '@/lib/types'

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000'

// Decompose task
export async function decomposeTask(data: { task_id: string }): Promise<OrchestrationCenterResponse> {
  console.log('Calling decomposeTask with data:', data)
  
  const response = await fetch(`${API_BASE_URL}/orchestrationCenter/decomposeTask`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(data),
  })

  if (!response.ok) {
    const errorText = await response.text()
    console.error('decomposeTask error:', response.status, errorText)
    throw new Error(`HTTP error! status: ${response.status}, message: ${errorText}`)
  }

  return await response.json()
}

// Assign agents to meta tasks by parent task ID
export async function assignAgentsToMetaTasks(data: { task_id: string }): Promise<OrchestrationCenterResponse> {
  console.log('Calling assignAgentsToMetaTasks with data:', data)
  
  const response = await fetch(`${API_BASE_URL}/orchestrationCenter/assignAgentsToMetaTasks`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(data),
  })

  if (!response.ok) {
    const errorText = await response.text()
    console.error('assignAgentsToMetaTasks error:', response.status, errorText)
    throw new Error(`HTTP error! status: ${response.status}, message: ${errorText}`)
  }

  return await response.json()
}

// Assign agent to meta task
export async function assignAgentToMetaTask(data: { 
  task_id: string, 
}): Promise<OrchestrationCenterResponse> {
  console.log('Calling assignAgentToMetaTask with data:', data)
  
  const response = await fetch(`${API_BASE_URL}/orchestrationCenter/assignAgentToMetaTask`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(data),
  })

  if (!response.ok) {
    const errorText = await response.text()
    console.error('assignAgentToMetaTask error:', response.status, errorText)
    throw new Error(`HTTP error! status: ${response.status}, message: ${errorText}`)
  }

  return await response.json()
}

// Run workflow
export async function runWorkflow(data: { task_id: string }): Promise<OrchestrationCenterResponse> {
  console.log('Calling runWorkflow with data:', data)
  
  const response = await fetch(`${API_BASE_URL}/orchestrationCenter/runWorkflow`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(data),
  })

  if (!response.ok) {
    const errorText = await response.text()
    console.error('runWorkflow error:', response.status, errorText)
    throw new Error(`HTTP error! status: ${response.status}, message: ${errorText}`)
  }

  return await response.json()
}

// Retry meta task
export async function retryMetaTask(data: { task_id: string }): Promise<OrchestrationCenterResponse> {
  console.log('Calling retryMetaTask with data:', data)
  
  const response = await fetch(`${API_BASE_URL}/orchestrationCenter/retryMetaTask`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(data),
  })

  if (!response.ok) {
    const errorText = await response.text()
    console.error('retryMetaTask error:', response.status, errorText)
    throw new Error(`HTTP error! status: ${response.status}, message: ${errorText}`)
  }

  return await response.json()
}

// Summarize meta task for base task
export async function summarizeMetaTaskForBaseTask(data: { task_id: string }): Promise<OrchestrationCenterResponse> {
  console.log('Calling summarizeMetaTaskForBaseTask with data:', data)
  
  const response = await fetch(`${API_BASE_URL}/orchestrationCenter/summarizeMetaTaskForBaseTask`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(data),
  })

  if (!response.ok) {
    const errorText = await response.text()
    console.error('summarizeMetaTaskForBaseTask error:', response.status, errorText)
    throw new Error(`HTTP error! status: ${response.status}, message: ${errorText}`)
  }

  return await response.json()
} 