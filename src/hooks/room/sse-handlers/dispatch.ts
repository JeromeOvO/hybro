import type { SSEMessage } from '@/lib/types/sse'
import {
  bufferCorrelatedEvent,
  CORRELATION_BUFFER_EVENT_TYPES,
  resolveSseCorrelation,
  TURN_CORRELATED_EVENT_TYPES,
} from './correlation'
import type { SSEHandlerDeps } from './types'
import { handleUserMessage } from './handlers/user-message'
import { handleAgentResponse } from './handlers/agent-response'
import { handleProcessingStatus } from './handlers/processing-status'
import { handleError, handleHeartbeat, handleRunEvent, handleTurnEvent } from './handlers/misc'
import { handleTaskSubmitted } from './handlers/task-submitted'
import { handleTaskUpdate } from './handlers/task-update'
import { handleArtifactUpdate } from './handlers/artifact-update'
import { handleHitlInputRequested, handleHitlStatusUpdate } from './handlers/hitl'

function needsCorrelationBuffer(type: SSEMessage['type']): boolean {
  return CORRELATION_BUFFER_EVENT_TYPES.has(type)
}

export function createSSEDispatcher(deps: SSEHandlerDeps) {
  const { lifecycle } = deps

  return async (sseMessage: SSEMessage) => {
    console.log('🔔 Room webhook received SSE message:', sseMessage)

    const correlation = resolveSseCorrelation(sseMessage, lifecycle)

    if (TURN_CORRELATED_EVENT_TYPES.has(sseMessage.type) && correlation.shouldDrop) {
      if (sseMessage.type === 'processing_status') {
        console.warn('🚫 [SSE] processing_status DROPPED (no client_request_id)', {
          status: sseMessage.data?.status,
          data: sseMessage.data,
        })
      }
      return
    }

    if (needsCorrelationBuffer(sseMessage.type)) {
      if (correlation.shouldBuffer && correlation.clientReqId) {
        if (sseMessage.type === 'processing_status') {
          console.log('📦 [SSE] processing_status BUFFERED', {
            status: sseMessage.data?.status,
            clientReqId: correlation.clientReqId,
          })
        }
        bufferCorrelatedEvent(correlation.clientReqId, sseMessage)
        return
      }
    }

    switch (sseMessage.type) {
      case 'user_message':
        handleUserMessage(deps, sseMessage)
        break
      case 'agent_response':
        await handleAgentResponse(deps, sseMessage)
        break
      case 'processing_status':
        handleProcessingStatus(deps, sseMessage, correlation)
        break
      case 'error':
        handleError(deps, sseMessage)
        break
      case 'heartbeat':
        handleHeartbeat()
        break
      case 'task_submitted':
        await handleTaskSubmitted(deps, sseMessage, correlation)
        break
      case 'task_update':
        await handleTaskUpdate(deps, sseMessage, correlation)
        break
      case 'artifact_update':
        handleArtifactUpdate({ roomId: deps.roomId, lifecycle }, sseMessage)
        break
      case 'hitl_input_requested':
        await handleHitlInputRequested(deps, sseMessage, correlation)
        break
      case 'hitl_status_update':
        handleHitlStatusUpdate(deps, sseMessage, correlation)
        break
      case 'turn_event':
        handleTurnEvent()
        break
      case 'run_event':
        handleRunEvent(deps, lifecycle, sseMessage)
        break
      default:
        console.log('❓ Unknown SSE message type:', sseMessage.type)
    }
  }
}
