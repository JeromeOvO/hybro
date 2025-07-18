// Task-related API functions
import type { TaskCenterResponse } from '@/lib/types'

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000'

// Query task
export async function queryTask(taskId: string): Promise<TaskCenterResponse> {
  const response = await fetch(`${API_BASE_URL}/task/queryTask/${taskId}`, {
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
  const response = await fetch(`${API_BASE_URL}/task/queryBaseTask/${taskId}`, {
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
  const response = await fetch(`${API_BASE_URL}/task/getAllSessions/${userName}`, {
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
  const response = await fetch(`${API_BASE_URL}/task/getBaseTasksBySessionId/${sessionId}`, {
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
  const response = await fetch(`${API_BASE_URL}/task/getMetaTasksByParentTaskId/${parentTaskId}`, {
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
