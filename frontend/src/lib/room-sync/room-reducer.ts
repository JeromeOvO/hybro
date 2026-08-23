// RoomReducer — single state entry for snapshot-driven room sync
// (Room Stream Snapshot plan §4, §8).
//
// State = the latest full snapshot plus the ordered deltas after it.
// Snapshots reconcile; deltas carry liveness. The reducer owns:
//   • capability detection off the `connected` handshake (room_seq present),
//   • pre-snapshot delta buffering + bootstrap snapshot recovery,
//   • ordered delta patch with a bounded reorder window and gap self-heal,
//   • snapshot application that folds the snapshot sections into the
//     message / streaming / trace stores (the same fold functions the live
//     handlers use are reused for buffered deltas and replay).
//
// Legacy behavior (no room_seq in `connected`) is preserved unchanged.

import type { AnySSEFrame, RoomSSEFrameMap, RoomSSEType, SnapshotData } from '@/lib/types/sse'
import { isRoomSSEType } from '@/lib/types/sse'
import { useMessageStore } from '@/stores/message-store'
import { useStreamingStore } from '@/stores/streaming-store'
import { useTraceStore } from '@/stores/trace-store'
import type { ArtifactData, MessageEntity } from '@/stores/message-store/types'

export const REORDER_WINDOW_MS = 500
export const BOOTSTRAP_SNAPSHOT_MS = 500
const PRE_SNAPSHOT_BUFFER_LIMIT = 400
const HEARTBEAT_GAP_SETTLE_MS = 300

export interface RoomReducerDeps {
  roomId: string
  /** Fold one delta frame through the live handler path (the fold path). */
  onDelta: (frame: AnySSEFrame) => Promise<void>
  /** Force a fresh snapshot: close the stream, reconnect with ?snapshot=1. */
  requestSnapshot: () => void
}

type DeltaType = Exclude<RoomSSEType, 'connected' | 'heartbeat' | 'snapshot'>
type DeltaFrame = RoomSSEFrameMap[DeltaType]

const PARTIAL_STREAM_ARTIFACT_SUFFIX = '-partial-stream'

function partialStreamArtifactId(messageId: string): string {
  return `${messageId}${PARTIAL_STREAM_ARTIFACT_SUFFIX}`
}

function textPartialToArtifact(messageId: string, content: string): ArtifactData {
  return {
    artifactId: partialStreamArtifactId(messageId),
    name: 'response',
    parts: [{ kind: 'text', text: content }],
    isStreaming: true,
  }
}

function frameRoomSeq(frame: AnySSEFrame): number | undefined {
  const data = frame.data as { room_seq?: unknown } | undefined
  return typeof data?.room_seq === 'number' ? data.room_seq : undefined
}

export class RoomReducer {
  private readonly deps: RoomReducerDeps
  private capabilityEnabled = false
  private snapshotApplied = false
  private lastRoomSeq: number | null = null
  private preSnapshotBuffer: DeltaFrame[] = []
  private reorderBuffer = new Map<number, DeltaFrame>()
  private reorderTimer: ReturnType<typeof setTimeout> | null = null
  private bootstrapTimer: ReturnType<typeof setTimeout> | null = null
  private heartbeatGapTimer: ReturnType<typeof setTimeout> | null = null
  private bootstrapRequested = false

  constructor(deps: RoomReducerDeps) {
    this.deps = deps
  }

  get enabled(): boolean {
    return this.capabilityEnabled
  }

  async handle(frame: AnySSEFrame): Promise<void> {
    if (!isRoomSSEType(frame.type)) return
    switch (frame.type) {
      case 'connected':
        await this.onConnected(frame as RoomSSEFrameMap['connected'])
        return
      case 'snapshot':
        await this.onSnapshot(frame as RoomSSEFrameMap['snapshot'])
        return
      case 'heartbeat':
        this.onHeartbeat(frame as RoomSSEFrameMap['heartbeat'])
        return
      default:
        await this.onDelta(frame as DeltaFrame)
    }
  }

  private async onConnected(frame: RoomSSEFrameMap['connected']): Promise<void> {
    // New session: reset per-connection ordering state.
    this.snapshotApplied = false
    this.lastRoomSeq = null
    this.preSnapshotBuffer = []
    this.reorderBuffer.clear()
    this.bootstrapRequested = false
    this.clearTimers()

    const roomSeq = frame.data.room_seq
    if (typeof roomSeq === 'number') {
      // Capability negotiation (§4): a room_seq in the handshake enables the
      // new snapshot-driven semantics.
      this.capabilityEnabled = true
      this.lastRoomSeq = roomSeq
      this.scheduleBootstrapCheck()
    } else {
      this.capabilityEnabled = false
    }
  }

  private async onSnapshot(frame: RoomSSEFrameMap['snapshot']): Promise<void> {
    if (!this.capabilityEnabled) return
    const watermark = frame.data.room_seq
    applySnapshotToStores(this.deps.roomId, frame.data)

    this.snapshotApplied = true
    this.lastRoomSeq = watermark
    if (this.bootstrapTimer) {
      clearTimeout(this.bootstrapTimer)
      this.bootstrapTimer = null
    }

    // Replay pre-snapshot deltas in order (plan rule 2): deltas at or below
    // the snapshot watermark are discarded; the rest re-enter the delta path.
    const buffered = this.preSnapshotBuffer
    this.preSnapshotBuffer = []
    for (const delta of buffered) {
      const seq = frameRoomSeq(delta)
      if (seq !== undefined && seq <= watermark) continue
      await this.onDelta(delta)
    }

    // Drain the reorder window: buffered higher-seq deltas replay in order
    // after the replacement snapshot applies (plan rule 3).
    await this.drainReorderBuffer()
  }

  private onHeartbeat(frame: RoomSSEFrameMap['heartbeat']): void {
    if (!this.capabilityEnabled || !this.snapshotApplied) return
    const roomSeq = frame.data.room_seq
    if (typeof roomSeq !== 'number') return
    if (this.lastRoomSeq !== null && roomSeq > this.lastRoomSeq + 1) {
      // A delta was missed while idle (plan §7): give the missing seq a
      // short settle window, then force a snapshot.
      if (!this.heartbeatGapTimer) {
        this.heartbeatGapTimer = setTimeout(() => {
          this.heartbeatGapTimer = null
          if (
            this.lastRoomSeq !== null &&
            roomSeq > this.lastRoomSeq + 1
          ) {
            this.deps.requestSnapshot()
          }
        }, HEARTBEAT_GAP_SETTLE_MS)
      }
    }
  }

  private async onDelta(frame: DeltaFrame): Promise<void> {
    if (!this.capabilityEnabled) {
      await this.deps.onDelta(frame)
      return
    }
    const seq = frameRoomSeq(frame)
    if (seq === undefined) {
      // Frames emitted by a pre-Phase-2 publisher instance carry no seq;
      // they bypass sequencing and fold directly.
      await this.deps.onDelta(frame)
      return
    }
    if (!this.snapshotApplied) {
      // Rule 2: deltas arriving before the first snapshot are buffered and
      // replayed in order after the snapshot applies. The bootstrap trigger
      // (rule 7) guarantees the snapshot actually arrives.
      if (this.preSnapshotBuffer.length < PRE_SNAPSHOT_BUFFER_LIMIT) {
        this.preSnapshotBuffer.push(frame)
      }
      this.requestBootstrapSnapshotOnce()
      return
    }
    if (this.lastRoomSeq !== null && seq <= this.lastRoomSeq) {
      // Stale delta already covered by the snapshot: discard (rule 3).
      return
    }
    if (this.lastRoomSeq !== null && seq === this.lastRoomSeq + 1) {
      await this.deps.onDelta(frame)
      this.lastRoomSeq = seq
      if (this.heartbeatGapTimer) {
        clearTimeout(this.heartbeatGapTimer)
        this.heartbeatGapTimer = null
      }
      await this.drainReorderBuffer()
      return
    }
    // Out-of-order delta: buffer in the bounded reorder window (§4 rule 3).
    if (!this.reorderBuffer.has(seq)) {
      this.reorderBuffer.set(seq, frame)
      if (!this.reorderTimer) {
        this.reorderTimer = setTimeout(() => {
          this.reorderTimer = null
          this.deps.requestSnapshot()
        }, REORDER_WINDOW_MS)
      }
    }
  }

  private async drainReorderBuffer(): Promise<void> {
    if (this.reorderBuffer.size === 0) return
    const expected = (this.lastRoomSeq ?? 0) + 1
    const next = this.reorderBuffer.get(expected)
    if (next === undefined) return
    this.reorderBuffer.delete(expected)
    await this.onDelta(next)
  }

  private scheduleBootstrapCheck(): void {
    if (this.bootstrapTimer) clearTimeout(this.bootstrapTimer)
    this.bootstrapTimer = setTimeout(() => {
      this.bootstrapTimer = null
      if (!this.snapshotApplied) {
        this.deps.requestSnapshot()
      }
    }, BOOTSTRAP_SNAPSHOT_MS)
  }

  private requestBootstrapSnapshotOnce(): void {
    if (this.bootstrapRequested || this.snapshotApplied) return
    this.bootstrapRequested = true
    // Rule 7: the first delta without a prior snapshot triggers recovery.
    this.deps.requestSnapshot()
  }

  private clearTimers(): void {
    for (const timer of [this.bootstrapTimer, this.reorderTimer, this.heartbeatGapTimer]) {
      if (timer) clearTimeout(timer)
    }
    this.bootstrapTimer = null
    this.reorderTimer = null
    this.heartbeatGapTimer = null
  }
}

// ── Snapshot application (fold snapshot sections into stores) ───────────────

function mapSnapshotStatusToTerminal(
  status: string | null,
): 'completed' | 'failed' | 'canceled' | undefined {
  if (status === 'completed') return 'completed'
  if (status === 'canceled') return 'canceled'
  if (status === 'failed' || status === 'rejected' || status === 'error' || status === 'rate_limited') {
    return 'failed'
  }
  return undefined
}

function applySnapshotMessages(roomId: string, snapshot: SnapshotData): void {
  const store = useMessageStore.getState()
  if (store.roomId && store.roomId !== roomId) return

  for (const message of snapshot.messages) {
    if (message.content !== null || message.agent_id) {
      // Agent-side record: upsert the committed message content.
      store.upsertMessage(
        {
          id: message.message_id,
          roomId,
          messageType: 'agent',
          content: message.content ?? '',
          senderName: message.agent_id ?? 'Agent',
          agentId: message.agent_id ?? undefined,
          clientRequestId: message.client_request_id ?? undefined,
          relatedMessageId: message.related_message_id ?? undefined,
          timestamp: message.ts ?? new Date().toISOString(),
          taskStatus: (message.task_status as never) ?? undefined,
          taskError: message.task_error ?? undefined,
          taskContent: message.task_content ?? undefined,
          taskRequiresInput: message.requires_input,
          taskRequiresAuth: message.requires_auth,
          stepNumber: message.step_number ?? undefined,
          totalSteps: message.total_steps ?? undefined,
          taskCreatedAt: message.created_at ?? undefined,
          isEphemeral: false,
        },
        'sse',
      )
    }
    if (message.status || message.status_logs.length > 0) {
      // Turn-level processing state belongs to the USER entity. Snapshot may
      // arrive before DB hydration; create a correlation-preserving shell so
      // the durable logs/status are never discarded because of arrival order.
      // DB hydration later fills content/sender fields and preserves the SSE
      // overlay through the normal message-store merge rules.
      let userEntity = useMessageStore.getState().entities[message.message_id]
      if (!userEntity || userEntity.messageType !== 'user') {
        const firstLogTimestamp = message.status_logs[0]?.timestamp
        store.upsertMessage(
          {
            id: message.message_id,
            roomId,
            messageType: 'user',
            content: '',
            senderName: 'You',
            timestamp: firstLogTimestamp || message.ts || new Date().toISOString(),
            clientRequestId: message.client_request_id ?? undefined,
            isEphemeral: false,
          },
          'sse',
        )
        userEntity = useMessageStore.getState().entities[message.message_id]
      }
      if (!userEntity || userEntity.messageType !== 'user') continue

      const turnTerminalStatus = mapSnapshotStatusToTerminal(message.status)
      if (turnTerminalStatus) {
        store.upsertMessage(
          {
            id: message.message_id,
            roomId,
            messageType: 'user',
            content: userEntity.content,
            senderName: userEntity.senderName,
            timestamp: userEntity.timestamp,
            clientRequestId: message.client_request_id ?? userEntity.clientRequestId,
            turnTerminalStatus,
          },
          'sse',
        )
      }
      for (const log of message.status_logs) {
        appendSnapshotStatusLog(
          roomId,
          userEntity,
          log.message,
          log.timestamp,
          log.turn_phase,
        )
      }
    }
  }
}

function appendSnapshotStatusLog(
  roomId: string,
  userEntity: MessageEntity,
  message: string,
  timestamp: string,
  turnPhase?: 'collecting' | 'synthesizing' | 'terminal',
): void {
  const trimmed = message.trim()
  if (!trimmed) return
  const store = useMessageStore.getState()
  const latest = store.entities[userEntity.id] ?? userEntity
  const existing = latest.processingStatusLogs ?? []
  if (existing.some((entry) => entry.message === trimmed)) return
  store.upsertMessage(
    {
      id: latest.id,
      roomId,
      messageType: 'user',
      content: latest.content,
      senderName: latest.senderName,
      timestamp: latest.timestamp,
      processingStatusLogs: [
        ...existing,
        {
          id: `processing-log-${timestamp}-${existing.length}`,
          message: trimmed,
          timestamp,
          ...(turnPhase ? { turnPhase } : {}),
        },
      ],
    },
    'sse',
  )
}

function applySnapshotStreaming(roomId: string, snapshot: SnapshotData): void {
  const streaming = useStreamingStore.getState()
  for (const [messageId, record] of Object.entries(snapshot.streaming)) {
    if (record.text) {
      streaming.append(
        messageId,
        roomId,
        textPartialToArtifact(messageId, record.text),
        false,
        {
          clientRequestId: record.client_request_id ?? undefined,
        },
      )
    }
    for (let index = 0; index < record.artifacts.length; index++) {
      streaming.append(
        messageId,
        roomId,
        record.artifacts[index] as unknown as ArtifactData,
        index > 0,
        {
          clientRequestId: record.client_request_id ?? undefined,
        },
      )
    }
    if (record.is_complete) {
      streaming.markComplete(messageId)
    }
  }
}

function applySnapshotHitl(
  roomId: string,
  snapshot: SnapshotData,
  hitlRequestIndex: Map<string, string>,
): void {
  const store = useMessageStore.getState()
  for (const request of snapshot.hitl.requests) {
    const requestId =
      typeof request.request_id === 'string' ? request.request_id : undefined
    const messageId =
      typeof request.message_id === 'string' ? request.message_id : undefined
    if (!requestId || !messageId) continue
    hitlRequestIndex.set(requestId, messageId)
    const entity = store.entities[messageId]
    if (!entity) continue
    store.upsertMessage(
      {
        id: messageId,
        roomId,
        messageType: 'agent',
        content: entity.content ?? '',
        senderName: entity.senderName ?? 'Agent',
        timestamp: entity.timestamp ?? new Date().toISOString(),
        hitlRequestId: requestId,
        hitlPrompt: typeof request.prompt === 'string' ? request.prompt : undefined,
        hitlPromptType: typeof request.prompt_type === 'string' ? (request.prompt_type as never) : undefined,
        hitlSource: typeof request.source === 'string' ? (request.source as never) : undefined,
        hitlInteractionId: typeof request.interaction_id === 'string' ? request.interaction_id : undefined,
        hitlInteractionStatus: typeof request.interaction_status === 'string' ? request.interaction_status : undefined,
        hitlInteractionVersion: typeof request.interaction_version === 'number' ? request.interaction_version : undefined,
        hitlApplicationStatus: typeof request.application_status === 'string' ? request.application_status : undefined,
        hitlResolved: request.status === 'responded' || request.status === 'resolved',
      },
      'sse',
    )
  }
}

function applySnapshotTrace(roomId: string, snapshot: SnapshotData): void {
  useTraceStore.getState().hydrateFromSnapshot(roomId, snapshot)
}

/** Snapshot replace: fold every snapshot section into the stores. */
export function applySnapshotToStores(
  roomId: string,
  snapshot: SnapshotData,
  hitlRequestIndex?: Map<string, string>,
): void {
  applySnapshotMessages(roomId, snapshot)
  applySnapshotStreaming(roomId, snapshot)
  applySnapshotTrace(roomId, snapshot)
  if (hitlRequestIndex) {
    applySnapshotHitl(roomId, snapshot, hitlRequestIndex)
  }
}
