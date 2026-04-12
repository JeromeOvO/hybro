'use client'

import { useCallback } from 'react'
import { useTurnEventStore } from '@/stores/turn-event-store'
import type { TurnEvent, TurnEventType } from '@/stores/turn-event-store/types'

/**
 * Transforms snake_case wire event to camelCase TurnEvent.
 * Exported so the SSE dispatcher can call it directly.
 */
export function camelCaseEvent(wire: Record<string, unknown>): TurnEvent {
  const envelope = {
    eventId: wire.event_id as string,
    turnId: wire.turn_id as string,
    seq: wire.seq as number,
    ts: wire.ts as number,
    type: wire.type as TurnEventType,
    ...(wire.client_request_id ? { clientRequestId: wire.client_request_id as string } : {}),
  }

  const type = wire.type as string

  switch (type) {
    case 'turn_started':
      return {
        ...envelope, type: 'turn_started',
        userInput: {
          text: (wire.user_input as Record<string, unknown>)?.text as string ?? '',
          attachments: (wire.user_input as Record<string, unknown>)?.attachments as [] ?? [],
        },
      }
    case 'turn_completed':
      return { ...envelope, type: 'turn_completed', durationMs: wire.duration_ms as number }
    case 'turn_failed':
      return {
        ...envelope, type: 'turn_failed',
        reason: wire.reason as string,
        ...(wire.code ? { code: wire.code as 'rate_limited' | 'error' | 'timeout' } : {}),
      }
    case 'turn_canceled':
      return { ...envelope, type: 'turn_canceled' }
    case 'phase_changed': {
      const phase = wire.phase as Record<string, unknown>
      return { ...envelope, type: 'phase_changed', phase: camelCasePhase(phase) }
    }
    case 'slot_opened':
      return {
        ...envelope, type: 'slot_opened',
        slotId: wire.slot_id as string,
        slotType: wire.slot_type as 'agent' | 'summary',
        ...(wire.agent_id ? { agentId: wire.agent_id as string } : {}),
        ...(wire.agent_name ? { agentName: wire.agent_name as string } : {}),
        ...(wire.mode ? { mode: wire.mode as 'supervisor' | 'debate' } : {}),
      }
    case 'slot_delta':
      return {
        ...envelope, type: 'slot_delta',
        slotId: wire.slot_id as string,
        textDelta: wire.text_delta as string,
      }
    case 'artifact_appended':
      return {
        ...envelope, type: 'artifact_appended',
        slotId: wire.slot_id as string,
        artifact: wire.artifact as any,
      }
    case 'slot_snapshot':
      return {
        ...envelope, type: 'slot_snapshot',
        slotId: wire.slot_id as string,
        content: wire.content as string,
        artifacts: wire.artifacts as [] ?? [],
      }
    case 'slot_terminated':
      return {
        ...envelope, type: 'slot_terminated',
        slotId: wire.slot_id as string,
        status: wire.status as 'completed' | 'failed' | 'canceled' | 'rejected',
        ...(wire.error ? { error: wire.error as string } : {}),
        ...(wire.has_partial_content != null ? { hasPartialContent: wire.has_partial_content as boolean } : {}),
      }
    case 'hitl_requested':
      return {
        ...envelope, type: 'hitl_requested',
        hitlId: wire.hitl_id as string,
        source: wire.source as 'supervisor' | 'agent',
        ...(wire.agent_name ? { agentName: wire.agent_name as string } : {}),
        prompt: wire.prompt as string,
        promptType: wire.prompt_type as 'text' | 'choice' | 'confirmation',
        ...(wire.choices ? { choices: wire.choices as string[] } : {}),
        ...(wire.group_id ? { groupId: wire.group_id as string } : {}),
        ...(wire.group_total != null ? { groupTotal: wire.group_total as number } : {}),
        ...(wire.group_index != null ? { groupIndex: wire.group_index as number } : {}),
      }
    case 'hitl_answered':
      return { ...envelope, type: 'hitl_answered', hitlId: wire.hitl_id as string, answer: wire.answer as string }
    case 'hitl_expired':
      return { ...envelope, type: 'hitl_expired', hitlId: wire.hitl_id as string }
    case 'hitl_canceled':
      return { ...envelope, type: 'hitl_canceled', hitlId: wire.hitl_id as string }
    case 'hitl_error':
      return { ...envelope, type: 'hitl_error', hitlId: wire.hitl_id as string, error: wire.error as string }
    default:
      return envelope as TurnEvent
  }
}

export function camelCasePhase(wire: Record<string, unknown>): any {
  const name = wire.name as string
  switch (name) {
    case 'delegating':
      return { name: 'delegating', agentNames: wire.agent_names as string[], count: wire.count as number }
    case 'round':
      return { name: 'round', current: wire.current as number, total: wire.total as number }
    case 'workflow_step':
      return { name: 'workflow_step', current: wire.current as number, total: wire.total as number, stepName: wire.step_name as string }
    default:
      return { name }
  }
}

/**
 * Hook that returns a callback to bridge SSE turn_event messages to the TurnEventLogManager.
 */
export function useSSEToEventLog(): (wireEvent: Record<string, unknown>) => void {
  const append = useTurnEventStore(s => s.append)

  return useCallback((wireEvent: Record<string, unknown>) => {
    const turnId = wireEvent.turn_id as string
    if (!turnId) return

    const event = camelCaseEvent(wireEvent)
    append(turnId, event)
  }, [append])
}
