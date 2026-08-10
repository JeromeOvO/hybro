import type { AnySSEFrame, RoomSSEMessage, RoomSSEType } from '@/lib/types/sse'
import { isConnectedData, isRoomSSEType } from '@/lib/types/sse'
import {
  bufferCorrelatedEvent,
  CORRELATION_BUFFER_EVENT_TYPES,
  resolveSseCorrelation,
  TURN_CORRELATED_EVENT_TYPES,
} from './correlation'
import type { SSEHandlerDeps } from './types'
import { handleAgentResponse, handleAgentResponsePartial } from './handlers/agent-response'
import { handleProcessingStatus } from './handlers/processing-status'
import {
  handleCancellation,
  handleConnected,
  handleError,
  handleHeartbeat,
  handleHubAgentEvent,
  handleRunEvent,
} from './handlers/misc'
import { handleTaskSubmitted } from './handlers/task-submitted'
import { handleTaskUpdate } from './handlers/task-update'
import { handleArtifactUpdate } from './handlers/artifact-update'
import { handleHitlRequest, handleHitlResponse } from './handlers/hitl'

export const HANDLED_ROOM_SSE_TYPES = {
  connected: true,
  heartbeat: true,
  processing_status: true,
  run_event: true,
  task_submitted: true,
  task_update: true,
  artifact_update: true,
  agent_response: true,
  agent_response_partial: true,
  error: true,
  hitl_request: true,
  hitl_response: true,
  cancellation: true,
  hub_agent_event: true,
} satisfies Record<RoomSSEType, true>

function needsCorrelationBuffer(type: RoomSSEType): boolean {
  return CORRELATION_BUFFER_EVENT_TYPES.has(type)
}

export function createSSEDispatcher(deps: SSEHandlerDeps) {
  const { lifecycle } = deps

  return async (sseMessage: AnySSEFrame) => {
    if (!isRoomSSEType(sseMessage.type)) {
      console.debug('Ignoring unknown room SSE frame type:', sseMessage.type, sseMessage)
      return
    }

    const roomMessage = sseMessage as RoomSSEMessage
    const correlation = resolveSseCorrelation(roomMessage, lifecycle)

    if (TURN_CORRELATED_EVENT_TYPES.has(roomMessage.type) && correlation.shouldDrop) {
      if (roomMessage.type === 'processing_status') {
        console.warn('🚫 [SSE] processing_status DROPPED (no client_request_id)', {
          status: roomMessage.data?.status,
          data: roomMessage.data,
        })
      }
      return
    }

    if (needsCorrelationBuffer(roomMessage.type)) {
      if (correlation.shouldBuffer && correlation.clientReqId) {
        bufferCorrelatedEvent(correlation.clientReqId, roomMessage)
        return
      }
    }

    switch (roomMessage.type) {
      case 'connected':
        if (!isConnectedData(roomMessage.data)) {
          console.debug('Ignoring malformed connected SSE frame:', roomMessage)
          return
        }
        handleConnected(roomMessage)
        break
      case 'agent_response':
        await handleAgentResponse(deps, roomMessage)
        break
      case 'agent_response_partial':
        handleAgentResponsePartial(deps, roomMessage, correlation)
        break
      case 'processing_status':
        handleProcessingStatus(deps, roomMessage, correlation)
        break
      case 'error':
        handleError(deps, roomMessage)
        break
      case 'heartbeat':
        handleHeartbeat()
        break
      case 'task_submitted':
        await handleTaskSubmitted(deps, roomMessage, correlation)
        break
      case 'task_update':
        await handleTaskUpdate(deps, roomMessage, correlation)
        break
      case 'artifact_update':
        handleArtifactUpdate({ roomId: deps.roomId, lifecycle }, roomMessage, correlation)
        break
      case 'hitl_request':
        await handleHitlRequest(deps, roomMessage, correlation)
        break
      case 'hitl_response':
        handleHitlResponse(deps, roomMessage, correlation)
        break
      case 'run_event':
        handleRunEvent(deps, lifecycle, roomMessage)
        break
      case 'cancellation':
        handleCancellation(deps, roomMessage)
        break
      case 'hub_agent_event':
        handleHubAgentEvent(roomMessage)
        break
      default:
        roomMessage satisfies never
    }
  }
}
