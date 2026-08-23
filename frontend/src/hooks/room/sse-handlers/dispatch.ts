import type { AnySSEFrame, RoomSSEFrameMap, RoomSSEMessage, RoomSSEType } from '@/lib/types/sse'
import { isRoomSSEType } from '@/lib/types/sse'
import { clientRequestIdOf, TURN_CORRELATED_EVENT_TYPES } from './client-request'
import type { SSEHandlerDeps } from './types'
import { handleAgentResponse, handleAgentResponsePartial } from './handlers/agent-response'
import { handleProcessingStatus } from './handlers/processing-status'
import {
  handleCancellation,
  handleError,
  handleHubAgentEvent,
  handleRunEvent,
} from './handlers/misc'
import { handleTaskSubmitted } from './handlers/task-submitted'
import { handleTaskUpdate } from './handlers/task-update'
import { handleArtifactUpdate } from './handlers/artifact-update'
import { handleHitlRequest, handleHitlResponse } from './handlers/hitl'
import { RoomReducer } from '@/lib/room-sync/room-reducer'

export const HANDLED_ROOM_SSE_TYPES = {
  connected: true,
  heartbeat: true,
  snapshot: true,
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

/** Frames the reducer routes as deltas (ordering is reducer-owned). */
type DeltaMessage = Exclude<
  RoomSSEMessage,
  | RoomSSEFrameMap['connected']
  | RoomSSEFrameMap['heartbeat']
  | RoomSSEFrameMap['snapshot']
>

/**
 * Fold one delta frame through the live handler path. This is the single
 * fold path shared by live deltas, buffered pre-snapshot deltas, and reorder
 * window replay (Room Stream Snapshot plan P4). Ordering and buffering are
 * reducer-owned; here only the client_request_id extraction happens.
 */
async function foldDelta(deps: SSEHandlerDeps, roomMessage: DeltaMessage): Promise<void> {
  const clientReqId = clientRequestIdOf(roomMessage)

  if (TURN_CORRELATED_EVENT_TYPES.has(roomMessage.type) && !clientReqId) {
    console.debug(
      'Dropping turn-correlated SSE event without client_request_id:',
      roomMessage.type,
    )
    return
  }

  switch (roomMessage.type) {
    case 'agent_response':
      await handleAgentResponse(deps, roomMessage)
      break
    case 'agent_response_partial':
      handleAgentResponsePartial(deps, roomMessage, clientReqId)
      break
    case 'processing_status':
      handleProcessingStatus(deps, roomMessage, clientReqId)
      break
    case 'error':
      handleError(deps, roomMessage)
      break
    case 'task_submitted':
      await handleTaskSubmitted(deps, roomMessage, clientReqId)
      break
    case 'task_update':
      await handleTaskUpdate(deps, roomMessage, clientReqId)
      break
    case 'artifact_update':
      handleArtifactUpdate({ roomId: deps.roomId, lifecycle: deps.lifecycle }, roomMessage, clientReqId)
      break
    case 'hitl_request':
      await handleHitlRequest(deps, roomMessage, clientReqId)
      break
    case 'hitl_response':
      handleHitlResponse(deps, roomMessage, clientReqId)
      break
    case 'run_event':
      handleRunEvent(deps, deps.lifecycle, roomMessage)
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

export function createSSEDispatcher(deps: SSEHandlerDeps) {
  // The reducer owns ordering: snapshot replace + ordered delta patch with
  // gap self-heal. The handlers above remain the fold functions.
  const reducer = new RoomReducer({
    roomId: deps.roomId,
    onDelta: (frame: AnySSEFrame) => {
      return foldDelta(deps, frame as DeltaMessage)
    },
    requestSnapshot: () => {
      const request = deps.requestSnapshotRef?.current
      if (request) {
        request()
      } else {
        console.warn('[SSE] snapshot recovery requested but no reconnect surface is bound')
      }
    },
  })

  return async (sseMessage: AnySSEFrame) => {
    if (!isRoomSSEType(sseMessage.type)) {
      console.debug('Ignoring unknown room SSE frame type:', sseMessage.type, sseMessage)
      return
    }
    await reducer.handle(sseMessage)
  }
}
