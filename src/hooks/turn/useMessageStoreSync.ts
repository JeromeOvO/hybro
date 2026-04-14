'use client'

import { useEffect, useRef } from 'react'
import { useMessageStore } from '@/stores/message-store'
import { useTurnEventStore } from '@/stores/turn-event-store'
import type { TurnEvent, ArtifactData } from '@/stores/turn-event-store/types'
import type { MessageEntity } from '@/stores/message-store/types'
import type { ArtifactPart } from '@/stores/message-store/types'

/**
 * Bridges the message store → turn event store in real time.
 *
 * When Redis is down the backend cannot emit turn_event SSE; only legacy
 * SSE events (processing_status, task_submitted, artifact_update, task_update)
 * arrive — these update the message store but leave the turn event store empty.
 *
 * This hook subscribes to message store version changes and incrementally
 * converts new/updated messages into turn events. It skips turns that
 * already exist from hydration and only pushes incremental updates
 * (content snapshots, terminal status) or creates brand-new turns.
 */
export function useMessageStoreSync() {
  const processedRef = useRef(new Map<string, number>()) // turnId → fingerprint

  useEffect(() => {
    const unsub = useMessageStore.subscribe(
      (s) => s.version,
      () => {
        const { entities, orderedIds } = useMessageStore.getState()
        const store = useTurnEventStore.getState()
        const processed = processedRef.current

        // Group entities into user and agent messages
        const userEntities: MessageEntity[] = []
        const agentEntities: MessageEntity[] = []

        for (const id of orderedIds) {
          const entity = entities[id]
          if (!entity || entity.isEphemeral) continue
          if (entity.messageType === 'user') userEntities.push(entity)
          else if (entity.messageType === 'agent') agentEntities.push(entity)
        }

        // Index agent messages by their related user message (turn) ID
        const agentsByTurn = new Map<string, MessageEntity[]>()
        const userIds = new Set(userEntities.map(u => u.id))
        const entityById = new Map(orderedIds.map(id => [id, entities[id]]))

        const unlinked: MessageEntity[] = []

        for (const agent of agentEntities) {
          const relId = agent.relatedMessageId
          if (!relId) { unlinked.push(agent); continue }

          let turnId: string | undefined
          if (userIds.has(relId)) {
            turnId = relId
          } else {
            // Chain routing: follow one level
            const related = entityById.get(relId)
            if (related?.relatedMessageId && userIds.has(related.relatedMessageId)) {
              turnId = related.relatedMessageId
            }
          }

          if (turnId) {
            const list = agentsByTurn.get(turnId) ?? []
            list.push(agent)
            agentsByTurn.set(turnId, list)
          } else {
            unlinked.push(agent)
          }
        }

        // Fallback: assign unlinked agents to the most recent user message.
        // This handles cases where relatedMessageId uses the server-assigned ID
        // but the user entity still has a temp ID (pre-swap timing gap).
        // Skip unlinked entities when a task-tracked entity with the same
        // agentId already exists in the turn (prevents duplicate from
        // agent_response SSE arriving after task_submitted).
        if (unlinked.length > 0 && userEntities.length > 0) {
          const lastUser = userEntities[userEntities.length - 1]
          const list = agentsByTurn.get(lastUser.id) ?? []
          for (const agent of unlinked) {
            const isDuplicate = agent.agentId && list.some(
              existing => existing.agentId === agent.agentId && existing.taskStatus != null,
            )
            if (!isDuplicate) {
              list.push(agent)
            }
          }
          agentsByTurn.set(lastUser.id, list)
        }

        // For each user message, check if this turn needs updating
        for (const userEntity of userEntities) {
          const turnId = userEntity.id
          const turnAgents = agentsByTurn.get(turnId) ?? []

          // Build a fingerprint to detect changes
          const fingerprint = computeFingerprint(userEntity, turnAgents)
          if (processed.get(turnId) === fingerprint) continue

          const existingLog = store.turnLogs.get(turnId)

          if (existingLog) {
            // Turn already exists — only push incremental updates
            pushIncrementalUpdates(store, turnId, existingLog, turnAgents)
          } else {
            // Brand new turn — create from scratch
            const events = buildTurnEvents(turnId, userEntity, turnAgents)
            for (const event of events) {
              store.append(turnId, event)
            }
          }

          processed.set(turnId, fingerprint)
        }

        // Clean up orphan optimistic turns that have been superseded by real turns
        cleanupOrphanOptimisticTurns(store, userIds)
      },
    )

    return unsub
  }, [])
}

/** Remove optimistic turns whose real counterpart now exists in the store. */
function cleanupOrphanOptimisticTurns(
  store: ReturnType<typeof useTurnEventStore.getState>,
  realUserIds: Set<string>,
) {
  // Optimistic turns use clientRequestId as turnId
  // Check each turnId — if it's NOT a real user message ID and has only a turn_started event,
  // and the real turn already exists, remove the orphan
  for (const turnId of store.orderedTurnIds) {
    if (realUserIds.has(turnId)) continue // real turn, skip
    const log = store.turnLogs.get(turnId)
    if (!log) continue
    const events = log.getEvents()
    // Optimistic turns have exactly 1 event (turn_started) with a clientRequestId
    if (events.length === 1 && events[0].type === 'turn_started') {
      const ev = events[0] as TurnEvent & { type: 'turn_started'; clientRequestId?: string }
      if (ev.clientRequestId) {
        // This is an optimistic turn — check if real turn exists
        // Real turnId would be a user message_id in the message store
        // We can't know the exact mapping, but if the user message exists in
        // the store (realUserIds), the optimistic one is orphaned
        store.removeTurn(turnId)
      }
    }
  }
}

/** Push only changed content/status for an existing turn, no duplicate structural events. */
function pushIncrementalUpdates(
  store: ReturnType<typeof useTurnEventStore.getState>,
  turnId: string,
  existingLog: ReturnType<typeof useTurnEventStore.getState>['turnLogs'] extends Map<string, infer V> ? V : never,
  agentEntities: MessageEntity[],
) {
  const existingEvents = existingLog.getEvents()
  let nextSeq = existingEvents.length > 0
    ? existingEvents[existingEvents.length - 1].seq + 100 // leave room for ordering
    : 1

  for (const agent of agentEntities) {
    const slotId = agent.id
    const slotTs = new Date(agent.timestamp).getTime() || Date.now()
    const slotType = classifySlotType(agent, agentEntities)

    // Ensure the slot is opened before pushing snapshots/termination.
    // Without slot_opened, the content-slots projection drops snapshots
    // and the rail projection never creates an item.
    const slotAlreadyOpened = existingEvents.some(
      e => e.type === 'slot_opened' && (e as TurnEvent & { slotId?: string }).slotId === slotId,
    )
    if (!slotAlreadyOpened) {
      store.append(turnId, {
        eventId: `sync_opened_${slotId}`,
        turnId,
        seq: nextSeq++,
        ts: slotTs,
        type: 'slot_opened',
        slotId,
        slotType,
        agentId: agent.agentId ?? '',
        agentName: undefined,
      } as TurnEvent)
    }

    // Push updated content snapshot (versioned eventId for dedup)
    if (agent.content) {
      store.append(turnId, {
        eventId: `sync_snap_${slotId}_v${agent.sourceVersion}`,
        turnId,
        seq: nextSeq++,
        ts: slotTs,
        type: 'slot_snapshot',
        slotId,
        content: agent.content,
        artifacts: convertArtifacts(agent.artifacts),
      } as TurnEvent)
    }

    // Push terminal status if agent is done
    const isTaskTerminal = agent.taskStatus === 'completed' || agent.taskStatus === 'failed'
      || agent.taskStatus === 'canceled' || agent.taskStatus === 'rejected'
    const isSynthesisComplete = slotType === 'summary' && agent.content
    if (isTaskTerminal || isSynthesisComplete) {
      const status = agent.taskStatus === 'rejected' ? 'rejected'
        : agent.taskStatus === 'canceled' ? 'canceled'
        : agent.taskStatus === 'failed' ? 'failed'
        : 'completed'
      store.append(turnId, {
        eventId: `sync_term_${slotId}`,
        turnId,
        seq: nextSeq++,
        ts: slotTs,
        type: 'slot_terminated',
        slotId,
        status,
      } as TurnEvent)
    }
  }

  // NOTE: Do NOT emit turn_completed here. Individual agent terminal
  // status does not mean the room-level processing is done (e.g. Supervisor
  // may continue evaluating/planning after an agent completes). The
  // authoritative signal is the processing_status terminal SSE event —
  // that handler emits turn_completed/turn_failed/turn_canceled.
  // buildTurnEvents() still emits turn_completed for historical data
  // loaded via hydration, where the processing is already finished.
}

/** Simple numeric fingerprint based on entity versions + count. */
function computeFingerprint(user: MessageEntity, agents: MessageEntity[]): number {
  let hash = user.sourceVersion * 1000000 + agents.length * 1000
  for (const a of agents) {
    hash += a.sourceVersion
  }
  return hash
}

/**
 * Classify agent entities into task-tracked (delegated agents) vs non-task
 * (synthesis from agent_response SSE). When both exist in a turn, the
 * non-task entities are supervisor/debate synthesis — rendered as 'summary'.
 */
function classifySlotType(agent: MessageEntity, allAgents: MessageEntity[]): 'agent' | 'summary' {
  const hasTaskStatus = agent.taskStatus != null
  if (hasTaskStatus) return 'agent'

  // Non-task entity — check if coexists with task-tracked entities
  const hasTaskTracked = allAgents.some(a => a.taskStatus != null)
  return hasTaskTracked ? 'summary' : 'agent'
}

/** Convert message entities to deterministic turn events (for new turns only). */
function buildTurnEvents(
  turnId: string,
  userEntity: MessageEntity,
  agentEntities: MessageEntity[],
): TurnEvent[] {
  let seq = 0
  const ts = new Date(userEntity.timestamp).getTime() || Date.now()
  const events: TurnEvent[] = []

  // turn_started — include clientRequestId so the store's append() can
  // detect and merge with an existing optimistic turn (prevents duplicate)
  events.push({
    eventId: `sync_started_${turnId}`,
    turnId,
    seq: ++seq,
    ts,
    type: 'turn_started',
    userInput: {
      text: userEntity.content || '',
      attachments: userEntity.attachments ?? [],
    },
    clientRequestId: userEntity.clientRequestId,
  } as TurnEvent)

  // For each agent: slot_opened + slot_snapshot + slot_terminated
  for (const agent of agentEntities) {
    const slotTs = new Date(agent.timestamp).getTime() || ts
    const slotId = agent.id
    const slotType = classifySlotType(agent, agentEntities)

    events.push({
      eventId: `sync_opened_${slotId}`,
      turnId,
      seq: ++seq,
      ts: slotTs,
      type: 'slot_opened',
      slotId,
      slotType,
      agentId: agent.agentId ?? '',
      agentName: undefined,
    } as TurnEvent)

    if (agent.content) {
      events.push({
        eventId: `sync_snap_${slotId}_v${agent.sourceVersion}`,
        turnId,
        seq: ++seq,
        ts: slotTs,
        type: 'slot_snapshot',
        slotId,
        content: agent.content,
        artifacts: convertArtifacts(agent.artifacts),
      } as TurnEvent)
    }

    // Synthesis entities (no taskStatus) with content are implicitly completed
    const isTaskTerminal = agent.taskStatus === 'completed' || agent.taskStatus === 'failed'
      || agent.taskStatus === 'canceled' || agent.taskStatus === 'rejected'
    const isSynthesisComplete = slotType === 'summary' && agent.content
    if (isTaskTerminal || isSynthesisComplete) {
      const status = agent.taskStatus === 'rejected' ? 'rejected'
        : agent.taskStatus === 'canceled' ? 'canceled'
        : agent.taskStatus === 'failed' ? 'failed'
        : 'completed'
      events.push({
        eventId: `sync_term_${slotId}`,
        turnId,
        seq: ++seq,
        ts: slotTs,
        type: 'slot_terminated',
        slotId,
        status,
      } as TurnEvent)
    }
  }

  const isEntityTerminal = (a: MessageEntity) =>
    a.taskStatus === 'completed' || a.taskStatus === 'failed'
    || a.taskStatus === 'canceled' || a.taskStatus === 'rejected'
    || (a.taskStatus == null && a.content) // synthesis with content = done
  const allTerminal = agentEntities.length > 0 && agentEntities.every(isEntityTerminal)
  if (allTerminal) {
    const lastTs = agentEntities.length > 0
      ? new Date(agentEntities[agentEntities.length - 1].timestamp).getTime() || ts
      : ts
    events.push({
      eventId: `sync_done_${turnId}`,
      turnId,
      seq: ++seq,
      ts: lastTs,
      type: 'turn_completed',
      durationMs: lastTs - ts,
    } as TurnEvent)
  }

  return events
}

/** Convert MessageEntity artifacts to TurnEvent ArtifactData. */
function convertArtifacts(artifacts?: MessageEntity['artifacts']): ArtifactData[] {
  if (!artifacts || artifacts.length === 0) return []
  return artifacts.map(a => ({
    artifactId: a.artifactId,
    name: a.name,
    parts: a.parts as ArtifactPart[],
  }))
}
