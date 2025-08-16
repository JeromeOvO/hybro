// Unified export of all types

import { Role } from './request'
import { BaseTask, MetaTask } from './response'

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
  ChatRequest,
  InspectionCenterRequest,
  TaskCenterRequest,
  UserInput,
  ChatMemoryRequest,
} from './request'

export type {
  AgentCenterResponse,
  ChatResponse,
  InspectionCenterResponse,
  TaskCenterResponse,
  InsepectionCenterConnectionValidationResponse,
  OrchestrationCenterResponse,
  TaskResponse,
  UserResponse,
  ChatMemoryResponse,
} from './response'

export * from './error'
export * from './health'

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
 