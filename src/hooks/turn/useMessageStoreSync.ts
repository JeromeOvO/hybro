'use client'

import { useEffect, useRef } from 'react'
import { useMessageStore } from '@/stores/message-store'
import { useTurnEventStore } from '@/stores/turn-event-store'
import type { TurnEvent, ArtifactData } from '@/stores/turn-event-store/types'
import type { MessageEntity } from '@/stores/message-store/types'
import type { ArtifactPart } from '@/stores/message-store/types'
import { isSystemAgent } from '@/lib/system-agents'

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

  // Clear bridge cache when turn store resets so re-hydration can rebuild turns
  useEffect(() => {
    let prevHydrated = useTurnEventStore.getState().hydrated
    const unsub = useTurnEventStore.subscribe((s) => {
      if (prevHydrated && !s.hydrated) {
        processedRef.current.clear()
      }
      prevHydrated = s.hydrated
    })
    return unsub
  }, [])

  useEffect(() => {
    const syncNow = () => {
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

      // Canonical routing: prefer direct clientRequestId on agent entities.
      // Keep relatedMessageId traversal as a bounded compatibility fallback
      // for legacy rows that may not carry clientRequestId yet.
      const agentsByTurn = new Map<string, MessageEntity[]>()
      const userByMessageId = new Map<string, MessageEntity>()
      const userMessageIds = new Set<string>()
      const entityById = new Map(orderedIds.map(id => [id, entities[id]]))
      for (const user of userEntities) {
        userByMessageId.set(user.id, user)
        userMessageIds.add(user.id)
      }

      const unlinked: MessageEntity[] = []

      for (const agent of agentEntities) {
        if (agent.clientRequestId) {
          const list = agentsByTurn.get(agent.clientRequestId) ?? []
          list.push(agent)
          agentsByTurn.set(agent.clientRequestId, list)
          continue
        }

        const relId = agent.relatedMessageId
        if (!relId) { unlinked.push(agent); continue }

        let userMessageId: string | undefined
        if (userMessageIds.has(relId)) {
          userMessageId = relId
        } else {
          // Chain routing: follow one level
          const related = entityById.get(relId)
          if (related?.relatedMessageId && userMessageIds.has(related.relatedMessageId)) {
            userMessageId = related.relatedMessageId
          }
        }

        const turnId = userMessageId ? userByMessageId.get(userMessageId)?.clientRequestId : undefined
        if (turnId) {
          const list = agentsByTurn.get(turnId) ?? []
          list.push(agent)
          agentsByTurn.set(turnId, list)
        } else {
          unlinked.push(agent)
        }
      }

      // Intentionally do not attach unlinked agents to a fallback turn.
      // Attaching to the most recent user can permanently mis-thread
      // append-only turn events when SSE arrives before user-id resolution.

      // For each user message, check if this turn needs updating
      for (const userEntity of userEntities) {
        const turnId = userEntity.clientRequestId
        if (!turnId) continue
        const turnAgents = agentsByTurn.get(turnId) ?? []

        // Build a fingerprint to detect changes
        const fingerprint = computeFingerprint(userEntity, turnAgents)
        if (processed.get(turnId) === fingerprint) continue

        const existingLog = store.turnLogs.get(turnId)

        if (existingLog) {
          // Turn already exists — only push incremental updates
          pushIncrementalUpdates(store, turnId, existingLog, turnAgents, userEntity)
        } else {
          // Brand new turn — create from scratch
          const events = buildTurnEvents(turnId, userEntity, turnAgents)
          for (const event of events) {
            store.append(turnId, event)
          }
        }

        processed.set(turnId, fingerprint)
      }

      // Keep only canonical turn ids that still exist in message-store.
      cleanupOrphanOptimisticTurns(store, new Set(userEntities.map(u => u.clientRequestId).filter((v): v is string => !!v)))
    }

    // Handle pre-populated message-store state immediately on mount so we do
    // not depend on a later version bump to seed the turn store.
    syncNow()

    const unsub = useMessageStore.subscribe(
      (s) => s.version,
      syncNow,
    )

    return unsub
  }, [])
}

/** Remove optimistic turns whose real counterpart now exists in the store.
 *
 * An "orphan" optimistic turn is any turn whose turnId is not a real user
 * message id (i.e. it is keyed by clientRequestId or a tempMessageId) but
 * whose clientRequestId now maps to a different, real turn. This can happen
 * when turn_event SSEs (e.g. slot_opened) create a turn at realMessageId
 * before the bridge's merge gets a chance to run, leaving the optimistic
 * turn stranded with accumulated events. We intentionally do NOT require
 * events.length === 1 — the optimistic turn may have accumulated slot_opened /
 * slot_snapshot events pushed by the bridge before the ID swap happened.
 */
function cleanupOrphanOptimisticTurns(
  store: ReturnType<typeof useTurnEventStore.getState>,
  canonicalTurnIds: Set<string>,
) {
  for (const turnId of store.orderedTurnIds) {
    if (!canonicalTurnIds.has(turnId)) {
      store.removeTurn(turnId)
    }
  }
}

/** Push only changed content/status for an existing turn, no duplicate structural events. */
function pushIncrementalUpdates(
  store: ReturnType<typeof useTurnEventStore.getState>,
  turnId: string,
  existingLog: ReturnType<typeof useTurnEventStore.getState>['turnLogs'] extends Map<string, infer V> ? V : never,
  agentEntities: MessageEntity[],
  userEntity: MessageEntity,
) {
  const existingEvents = existingLog.getEvents()
  let nextSeq = existingEvents.length > 0
    ? existingEvents[existingEvents.length - 1].seq + 100 // leave room for ordering
    : 1

  // Process HITL entities before regular agents so HITL Q&A cards render
  // above agent response blocks (HITL happens during processing, before completion).
  const ordered = orderEntitiesHitlFirst(agentEntities)

  for (const agent of ordered) {
    // HITL entities → emit HITL turn events, skip regular slot creation.
    if (detectHitlEntity(agent)) {
      const hitlId = agent.hitlRequestId || `hitl_db_${agent.id}`
      const slotTs = new Date(agent.timestamp).getTime() || Date.now()
      const hitlAlreadyEmitted = existingEvents.some(
        e => e.type === 'hitl_requested' && (e as TurnEvent & { type: 'hitl_requested'; hitlId: string }).hitlId === hitlId,
      )
      if (!hitlAlreadyEmitted) {
        store.append(turnId, {
          eventId: `sync_hitl_req_${hitlId}`,
          turnId,
          seq: nextSeq++,
          ts: slotTs,
          type: 'hitl_requested',
          hitlId,
          source: 'agent' as const,
          agentName: resolveHitlAgentName(agent, agentEntities),
          prompt: agent.hitlPrompt || agent.content || '',
          promptType: (agent.hitlPromptType || 'text') as 'text' | 'choice' | 'confirmation',
          choices: agent.hitlChoices ?? undefined,
          groupId: agent.hitlGroupId,
          groupTotal: agent.hitlGroupTotal,
          groupIndex: agent.hitlGroupIndex,
        } as TurnEvent)
      }
      if (agent.hitlResolved && agent.hitlUserAnswer) {
        store.append(turnId, {
          eventId: `sync_hitl_ans_${hitlId}`,
          turnId,
          seq: nextSeq++,
          ts: slotTs,
          type: 'hitl_answered',
          hitlId,
          answer: agent.hitlUserAnswer,
        } as TurnEvent)
      } else if (agent.hitlResolved) {
        store.append(turnId, {
          eventId: `sync_hitl_exp_${hitlId}`,
          turnId,
          seq: nextSeq++,
          ts: slotTs,
          type: 'hitl_expired',
          hitlId,
        } as TurnEvent)
      }
      continue
    }

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
    const hasArtifacts = agent.artifacts && agent.artifacts.length > 0
    if (agent.content || hasArtifacts) {
      store.append(turnId, {
        eventId: `sync_snap_${slotId}_v${agent.sourceVersion}`,
        turnId,
        seq: nextSeq++,
        ts: slotTs,
        type: 'slot_snapshot',
        slotId,
        content: agent.content || '',
        artifacts: convertArtifacts(agent.artifacts),
        hydrated: agent.source !== 'sse',
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

  // Derive turn-level terminal event from the room-level signal stamped on
  // the user entity by the processing_status SSE handler.
  //
  // Individual agent slot_terminated events do NOT imply the room is done —
  // the supervisor may continue after one agent completes. Only the
  // processing_status terminal event (proxied here via turnTerminalStatus on
  // the user entity) is the authoritative room-level completion signal.
  //
  // buildTurnEvents() still emits turn_completed for historical/hydrated turns
  // using the allTerminal heuristic, since those are already complete at load time.
  const turnAlreadyTerminal = existingEvents.some(
    e => e.type === 'turn_completed' || e.type === 'turn_failed' || e.type === 'turn_canceled',
  )
  if (!turnAlreadyTerminal && userEntity.turnTerminalStatus) {
    const type =
      userEntity.turnTerminalStatus === 'failed'   ? 'turn_failed'   :
      userEntity.turnTerminalStatus === 'canceled' ? 'turn_canceled' : 'turn_completed'
    store.append(turnId, {
      eventId: `sync_terminal_${turnId}`,
      turnId,
      seq: nextSeq++,
      ts: Date.now(),
      type,
      durationMs: 0,
      ...(type === 'turn_failed' ? { reason: 'processing_failed', code: 'error' } : {}),
    } as TurnEvent)
  }
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

/**
 * Resolve the actual requesting agent name for a HITL entity.
 *
 * When HITL is stored in DB with agentId 'supervisor_hitl', the senderName
 * becomes "Question & Answer". We look for the real requesting agent among
 * sibling entities in the same turn — the non-HITL, non-system agent that
 * triggered the HITL.
 */
export function resolveHitlAgentName(
  hitlEntity: MessageEntity,
  allAgents: MessageEntity[],
): string | undefined {
  // If the HITL entity already has a real agent name (not a system agent), use it
  if (hitlEntity.agentId && !isSystemAgent(hitlEntity.agentId)) {
    return hitlEntity.senderName || undefined
  }
  // Find a non-HITL, non-system agent in the same turn
  const requestingAgent = allAgents.find(
    a => a !== hitlEntity
      && a.agentId
      && !isSystemAgent(a.agentId)
      && a.taskStatus !== 'input-required',
  )
  return requestingAgent?.senderName || hitlEntity.senderName || undefined
}

/**
 * Detect whether an entity is HITL.
 * Checks hitlRequestId (set by SSE/overlay) or taskStatus 'input-required'
 * (always present on DB-hydrated HITL entities even when hitlRequestId is missing).
 */
function detectHitlEntity(agent: MessageEntity): boolean {
  return !!(agent.hitlRequestId || agent.taskStatus === 'input-required')
}

/**
 * Order agent entities so HITL entities come before regular agents.
 * HITL Q&A is part of the "gathering information" phase and should render
 * above the agent's final response in the content area.
 */
function orderEntitiesHitlFirst(agents: MessageEntity[]): MessageEntity[] {
  const hitl: MessageEntity[] = []
  const regular: MessageEntity[] = []
  for (const a of agents) {
    if (detectHitlEntity(a)) hitl.push(a)
    else regular.push(a)
  }
  return [...hitl, ...regular]
}

/** Convert message entities to deterministic turn events (for new turns only). */
export function buildTurnEvents(
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

  // Process HITL entities before regular agents so HITL Q&A cards render
  // above agent response blocks (HITL happens during processing, before completion).
  const ordered = orderEntitiesHitlFirst(agentEntities)

  // For each agent: slot_opened + slot_snapshot + slot_terminated
  for (const agent of ordered) {
    // HITL entities → emit HITL turn events, skip regular slot creation.
    if (detectHitlEntity(agent)) {
      const hitlId = agent.hitlRequestId || `hitl_db_${agent.id}`
      const hitlTs = new Date(agent.timestamp).getTime() || ts
      events.push({
        eventId: `sync_hitl_req_${hitlId}`,
        turnId,
        seq: ++seq,
        ts: hitlTs,
        type: 'hitl_requested',
        hitlId,
        source: 'agent' as const,
        agentName: resolveHitlAgentName(agent, agentEntities),
        prompt: agent.hitlPrompt || agent.content || '',
        promptType: (agent.hitlPromptType || 'text') as 'text' | 'choice' | 'confirmation',
        choices: agent.hitlChoices ?? undefined,
        groupId: agent.hitlGroupId,
        groupTotal: agent.hitlGroupTotal,
        groupIndex: agent.hitlGroupIndex,
      } as TurnEvent)
      if (agent.hitlResolved && agent.hitlUserAnswer) {
        events.push({
          eventId: `sync_hitl_ans_${hitlId}`,
          turnId,
          seq: ++seq,
          ts: hitlTs,
          type: 'hitl_answered',
          hitlId,
          answer: agent.hitlUserAnswer,
        } as TurnEvent)
      } else if (agent.hitlResolved) {
        events.push({
          eventId: `sync_hitl_exp_${hitlId}`,
          turnId,
          seq: ++seq,
          ts: hitlTs,
          type: 'hitl_expired',
          hitlId,
        } as TurnEvent)
      }
      continue
    }

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

    const agentHasArtifacts = agent.artifacts && agent.artifacts.length > 0
    if (agent.content || agentHasArtifacts) {
      events.push({
        eventId: `sync_snap_${slotId}_v${agent.sourceVersion}`,
        turnId,
        seq: ++seq,
        ts: slotTs,
        type: 'slot_snapshot',
        slotId,
        content: agent.content || '',
        artifacts: convertArtifacts(agent.artifacts),
        hydrated: agent.source !== 'sse',
      } as TurnEvent)
    }

    // Synthesis entities (no taskStatus) with content are implicitly completed
    const isTaskTerminal = agent.taskStatus === 'completed' || agent.taskStatus === 'failed'
      || agent.taskStatus === 'canceled' || agent.taskStatus === 'rejected'
    const isSynthesisComplete = slotType === 'summary' && (agent.content || agentHasArtifacts)
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
    || ((a.hitlRequestId != null || a.taskStatus === 'input-required') && a.hitlResolved)
    || (a.taskStatus == null && (a.content || (a.artifacts && a.artifacts.length > 0))) // synthesis with content/artifacts = done
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
