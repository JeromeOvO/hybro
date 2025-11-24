// Task-related API functions
import type { TaskCenterResponse } from '@/lib/types'

import { getApiUrl } from '../utils'
import { getClientAuthHeaders } from '../auth'

const API_BASE_URL = getApiUrl('task')

// Query task
export async function queryTask(
  taskId: string,
  getToken?: () => Promise<string | null>
): Promise<TaskCenterResponse> {
  const headers = await getClientAuthHeaders(getToken)
  const response = await fetch(`${API_BASE_URL}/queryTask/${taskId}`, {
    method: 'GET',
    headers,
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  return await response.json()
}

// Query base task
export async function queryBaseTask(
  taskId: string,
  getToken?: () => Promise<string | null>
): Promise<TaskCenterResponse> {
  const headers = await getClientAuthHeaders(getToken)
  const response = await fetch(`${API_BASE_URL}/queryBaseTask/${taskId}`, {
    method: 'GET',
    headers,
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  return await response.json()
}

// Get all sessions
export async function getAllSessions(
  userName: string,
  getToken?: () => Promise<string | null>
): Promise<TaskCenterResponse> {
  const headers = await getClientAuthHeaders(getToken)
  const response = await fetch(`${API_BASE_URL}/getAllSessions/${userName}`, {
    method: 'GET',
    headers,
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  return await response.json()
}

// Get base tasks by session ID
export async function getBaseTasksBySessionId(
  sessionId: string,
  getToken?: () => Promise<string | null>
): Promise<TaskCenterResponse> {
  const headers = await getClientAuthHeaders(getToken)
  const response = await fetch(`${API_BASE_URL}/getBaseTasksBySessionId/${sessionId}`, {
    method: 'GET',
    headers,
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  return await response.json()
}

// Get meta tasks by parent task ID - Fix API path
export async function getMetaTasksByParentId(
  parentTaskId: string,
  getToken?: () => Promise<string | null>
): Promise<TaskCenterResponse> {
  const headers = await getClientAuthHeaders(getToken)
  const response = await fetch(`${API_BASE_URL}/getMetaTasksByParentTaskId/${parentTaskId}`, {
    method: 'GET',
    headers,
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  return await response.json()
}
