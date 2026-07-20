/**
 * A2A Tasks API Client
 * 
 * API client for querying long-running A2A task status.
 */

import { getApiUrl } from '../utils'
import { getClientAuthHeaders } from '../auth'
import type { TaskState } from '../types/sse'

const API_BASE_URL = getApiUrl('a2a-tasks')

export interface A2ATaskStatus {
  message_id: string
  status: TaskState
  task: {
    id: string
    contextId?: string
    status: {
      state: TaskState
      message?: {
        role: string
        parts: Array<{ text?: string }>
      }
      timestamp?: string
    }
    artifacts?: Array<{
      artifactId: string
      name: string
      parts: Array<{ text?: string }>
    }>
    history?: Array<{
      role: string
      parts: Array<{ text?: string }>
    }>
  }
  agent_name?: string
  agent_id?: string
  related_message_id?: string | null
  created_at: string
  updated_at: string
  retry_after_seconds: number | null
}

export interface A2ATaskListItem {
  message_id: string
  task_id: string
  agent_name?: string
  agent_id?: string
  related_message_id?: string | null
  status: TaskState
  created_at: string
  updated_at: string
}

export interface GetTaskStatusResponse {
  success: boolean
  error?: string
  task?: A2ATaskStatus
}

export interface ListTasksResponse {
  success: boolean
  error?: string
  tasks?: A2ATaskListItem[]
}

/**
 * Get the status of a long-running A2A task.
 */
export async function getTaskStatus(
  internalId: string,
  getToken?: () => Promise<string | null>
): Promise<GetTaskStatusResponse> {
  try {
    const headers = await getClientAuthHeaders(getToken)
    const response = await fetch(`${API_BASE_URL}/${internalId}`, {
      method: 'GET',
      headers,
    })

    if (!response.ok) {
      if (response.status === 404) {
        return { success: false, error: 'Task not found' }
      }
      throw new Error(`HTTP error! status: ${response.status}`)
    }

    const data = await response.json()
    return { success: true, task: data }
  } catch (error) {
    console.error('Failed to get task status:', error)
    return {
      success: false,
      error: error instanceof Error ? error.message : 'Unknown error',
    }
  }
}

/**
 * List all A2A tasks for a room.
 */
export async function listRoomTasks(
  roomId: string,
  limit: number = 50,
  getToken?: () => Promise<string | null>
): Promise<ListTasksResponse> {
  try {
    const headers = await getClientAuthHeaders(getToken)
    const url = `${getApiUrl('rooms')}/${roomId}/a2a-tasks?limit=${limit}`
    const response = await fetch(url, {
      method: 'GET',
      headers,
    })

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`)
    }

    const data = await response.json()
    return { success: true, tasks: data.tasks }
  } catch (error) {
    console.error('Failed to list room tasks:', error)
    return {
      success: false,
      error: error instanceof Error ? error.message : 'Unknown error',
    }
  }
}

/**
 * List all pending A2A tasks for the current user.
 */
export async function listUserPendingTasks(
  getToken?: () => Promise<string | null>
): Promise<ListTasksResponse> {
  try {
    const headers = await getClientAuthHeaders(getToken)
    const url = `${getApiUrl('users')}/me/a2a-tasks`
    const response = await fetch(url, {
      method: 'GET',
      headers,
    })

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`)
    }

    const data = await response.json()
    return { success: true, tasks: data.tasks }
  } catch (error) {
    console.error('Failed to list user pending tasks:', error)
    return {
      success: false,
      error: error instanceof Error ? error.message : 'Unknown error',
    }
  }
}

/**
 * Extract text content from task artifacts.
 */
export function extractTaskContent(task: A2ATaskStatus['task']): string | undefined {
  const texts: string[] = []

  if (task.artifacts) {
    for (const artifact of task.artifacts) {
      for (const part of artifact.parts || []) {
        // Handle both direct text and root-wrapped text (Pydantic RootModel)
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const anyPart = part as any
        const text = anyPart.text || anyPart.root?.text
        if (text) {
          texts.push(text)
        }
      }
    }
  }

  return texts.length > 0 ? texts.join('') : undefined
}

/**
 * Extract error message from task status.
 */
export function extractTaskError(task: A2ATaskStatus['task']): string | undefined {
  const parts = task.status?.message?.parts
  if (!parts || parts.length === 0) return undefined
  // Handle both direct text and root-wrapped text (Pydantic RootModel)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const anyPart = parts[0] as any
  return anyPart.text || anyPart.root?.text
}
