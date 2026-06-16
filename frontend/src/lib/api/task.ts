// Task-related API functions
import type { TaskCenterResponse } from '@/lib/types'

import { getApiUrl } from '../utils'
import { apiGet } from '../api-client'

const API_BASE_URL = getApiUrl('task')

// Query task
export async function queryTask(
  taskId: string,
  getToken?: () => Promise<string | null>
): Promise<TaskCenterResponse> {
  return apiGet<TaskCenterResponse>(
    `${API_BASE_URL}/queryTask/${taskId}`,
    getToken
  )
}

// Query base task
export async function queryBaseTask(
  taskId: string,
  getToken?: () => Promise<string | null>
): Promise<TaskCenterResponse> {
  return apiGet<TaskCenterResponse>(
    `${API_BASE_URL}/queryBaseTask/${taskId}`,
    getToken
  )
}

// Get all sessions
export async function getAllSessions(
  userName: string,
  getToken?: () => Promise<string | null>
): Promise<TaskCenterResponse> {
  return apiGet<TaskCenterResponse>(
    `${API_BASE_URL}/getAllSessions/${userName}`,
    getToken
  )
}

// Get base tasks by session ID
export async function getBaseTasksBySessionId(
  sessionId: string,
  getToken?: () => Promise<string | null>
): Promise<TaskCenterResponse> {
  return apiGet<TaskCenterResponse>(
    `${API_BASE_URL}/getBaseTasksBySessionId/${sessionId}`,
    getToken
  )
}

// Get meta tasks by parent task ID
export async function getMetaTasksByParentId(
  parentTaskId: string,
  getToken?: () => Promise<string | null>
): Promise<TaskCenterResponse> {
  return apiGet<TaskCenterResponse>(
    `${API_BASE_URL}/getMetaTasksByParentTaskId/${parentTaskId}`,
    getToken
  )
}
