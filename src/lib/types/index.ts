// Unified export of all types

import type { Role } from './response'
import type { BaseTask, MetaTask } from './response'

// Core types from generated files - using explicit exports to avoid conflicts
export type {
  Agent,
  AgentCard,
  AgentSkill,
  AgentCapabilities,
  AgentStatus,
  Role,
  TaskState,
  Part,
  Message,
  Task,
  TaskSession,
  TaskStatus,
  Artifact,
  TextPart,
  FilePart,
  DataPart,
  SecurityScheme,
  APIKeySecurityScheme,
  HTTPAuthSecurityScheme,
  OAuth2SecurityScheme,
  OpenIdConnectSecurityScheme,
  In,
  MetaTask,
  BaseTask
} from './response'

export type {
  AgentCenterRequest,
  InspectionCenterRequest,
  TaskCenterRequest,
  UserInput,
  ChatMemoryRequest,
  APIKeyCreateRequest,
} from './request'

export type {
  AgentCenterResponse,
  APIKeyCreateResponse,
  APIKeyItemResponse,
  APIKeyListResponse,
  APIKeyOperationResponse,
  InspectionCenterResponse,
  TaskCenterResponse,
  InsepectionCenterConnectionValidationResponse,
  OrchestrationResponse,
  TaskResponse,
  UserResponse,
  ChatMemoryResponse,
} from './response'

export * from './error'
export * from './health'

// Room message type discriminator (user message, agent response, or A2A task)
export type MessageType = "user" | "agent" | "task"

export const MESSAGE_TYPE = {
  USER: "user",
  AGENT: "agent",
  TASK: "task",
} as const

// Message data for UI components
export interface MessageData {
  id: string
  content: string
  role: Role
  timestamp: Date
  taskId?: string
  isThinking?: boolean
  sender?: {
    name: string
    avatar?: string
    id?: string
  }
  isLoading?: boolean
  error?: string
  // Add workflow related fields
  messageType?: 'text' | 'workflow'
  workflowData?: {
    baseTask: BaseTask
    metaTasks: MetaTask[]
  }
}

// API Response wrapper
export interface ApiResponse<T = unknown> {
  success: boolean
  error: string | null
  status_code: number
  data?: T
}
 