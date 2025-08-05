// Task-related API functions
import type { TaskCenterResponse } from '@/lib/types'

// Using Next.js API routes as proxy to avoid CORS issues
const API_BASE_URL = '/api/task'

// Query task
export async function queryTask(taskId: string): Promise<TaskCenterResponse> {
  const response = await fetch(`${API_BASE_URL}/queryTask/${taskId}`, {
    method: 'GET',
    headers: {
      'Content-Type': 'application/json',
    },
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  return await response.json()
}

// Query base task
export async function queryBaseTask(taskId: string): Promise<TaskCenterResponse> {
  const response = await fetch(`${API_BASE_URL}/queryBaseTask/${taskId}`, {
    method: 'GET',
    headers: {
      'Content-Type': 'application/json',
    },
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  return await response.json()
}

// Get all sessions
export async function getAllSessions(userName: string): Promise<TaskCenterResponse> {
  const response = await fetch(`${API_BASE_URL}/getAllSessions/${userName}`, {
    method: 'GET',
    headers: {
      'Content-Type': 'application/json',
    },
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  return await response.json()
}

// Get base tasks by session ID
export async function getBaseTasksBySessionId(sessionId: string): Promise<TaskCenterResponse> {
  const response = await fetch(`${API_BASE_URL}/getBaseTasksBySessionId/${sessionId}`, {
    method: 'GET',
    headers: {
      'Content-Type': 'application/json',
    },
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  return await response.json()
}

// Get meta tasks by parent task ID - Fix API path
export async function getMetaTasksByParentId(parentTaskId: string): Promise<TaskCenterResponse> {
  const response = await fetch(`${API_BASE_URL}/getMetaTasksByParentTaskId/${parentTaskId}`, {
    method: 'GET',
    headers: {
      'Content-Type': 'application/json',
    },
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  return await response.json()
}
