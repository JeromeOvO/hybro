import { useEffect } from 'react'
import type { ProcessingLifecycle } from './processing-lifecycle'
import { ensureInitialProcessingStatusLog } from './processing-status-log'
import { useMessageStore } from '@/stores/message-store'
import { useRoomUiStore } from '@/stores/room-ui-store'
import { allAgentsTerminalForUserMessage } from '@/lib/room-timeline/turn-agent-terminal'
import { ensureTurnTerminalStampedFromBackendTruth } from '@/lib/room-timeline/turn-terminal-stamp'
import { inquiryActiveRuns } from '@/lib/api/room'
import { isTerminalState } from '@/lib/types/sse'
import { isStale } from '@/lib/time'

interface ProcessingSnapshotRoom {
  active_runs?: Array<{ trigger_message_id?: string | null }> | null
}

export function useProcessingRestore(
  roomId: string,
  room: ProcessingSnapshotRoom | null,
  queryLoading: boolean,
  lifecycle: ProcessingLifecycle,
  getToken?: (() => Promise<string | null>) | undefined,
  reconcileWithDb?: ((roomId: string) => Promise<unknown>) | undefined,
) {
  // Reactive subscription ensures the effect re-fires when hydration completes,
  // even if room/queryLoading settled first.
  const hydratedFromDb = useMessageStore(s => s.hydratedFromDb)

  useEffect(() => {
    // Only run when room data is loaded and not currently loading
    if (!room || queryLoading) return

    // Wait for DB hydration to complete before checking for task messages
    if (!hydratedFromDb) return
    const store = useMessageStore.getState()
    if (store.roomId !== roomId) return

    // Once SSE has resolved the live processing lifecycle, never re-add it —
    // the restore effect is only for page-load recovery.
    if (lifecycle.isProcessingResolved()) return

    const activeRuns = room.active_runs ?? []
    const activeRunTriggerMessageId =
      activeRuns.find((run) => !!run.trigger_message_id)?.trigger_message_id ?? null
    const lifecycleMessageId = activeRunTriggerMessageId
    const hasActiveLifecycle = activeRuns.length > 0

    const finishStaleActiveRunIfTerminal = async () => {
      if (!lifecycleMessageId) return
      if (!allAgentsTerminalForUserMessage(store.entities, roomId, lifecycleMessageId)) return

      let backendRunActive = true
      if (getToken) {
        try {
          const result = await inquiryActiveRuns(roomId, getToken)
          if (result.success) {
            backendRunActive = (result.active_runs ?? []).some(
              run => run.trigger_message_id === lifecycleMessageId,
            )
          }
        } catch {
          backendRunActive = true
        }
      }

      if (backendRunActive) return

      await ensureTurnTerminalStampedFromBackendTruth(
        roomId,
        lifecycle,
        { relatedMessageId: lifecycleMessageId },
        getToken,
      )

      const { sending } = useRoomUiStore.getState().rooms[roomId] ?? {}
      if (!sending) {
        store.removeMessage(lifecycle.placeholderId(roomId))
        lifecycle.stopProcessing()
      }
    }

    // Check if room has an active processing state
    if (hasActiveLifecycle) {
      // Always restore the message ID so cancellation works after refresh.
      lifecycle.setMessageId(lifecycleMessageId)

      // Cached active_runs may be stale — verify with backend before stopping processing.
      if (lifecycleMessageId) {
        void finishStaleActiveRunIfTerminal()
      }

      if (
        lifecycleMessageId
        && allAgentsTerminalForUserMessage(store.entities, roomId, lifecycleMessageId)
      ) {
        return
      }

      // Check if the triggering user message is stale (> 2 min).
      if (!lifecycleMessageId) {
        lifecycle.startProcessing(null)
        return
      }

      // Check if the triggering user message is stale (> 2 min).
      const PLACEHOLDER_STALE_MS = 2 * 60 * 1000
      const triggerMsg = store.entities[lifecycleMessageId]
      if (!triggerMsg) {
        return
      }
      if (isStale(triggerMsg.timestamp, PLACEHOLDER_STALE_MS)) {
        return
      }

      // Check if any active (non-terminal) task entities already exist in the store
      const hasTaskEntities = Object.values(store.entities).some(
        e => e.roomId === roomId && e.taskStatus && !isTerminalState(e.taskStatus)
      )

      if (hasTaskEntities) {
        return
      }

      store.removeMessage(lifecycle.placeholderId(roomId))
      ensureInitialProcessingStatusLog(roomId, triggerMsg)
      lifecycle.startProcessing(lifecycleMessageId)
    } else {
      // Backend truth says no active processing — clean up lifecycle state left
      // behind by missed terminal SSE without deleting per-turn update history.
      // But don't wipe it if a message send is still in flight (the SSE events
      // haven't arrived yet).
      const { sending } = useRoomUiStore.getState().rooms[roomId] ?? {}
      if (sending) return
      if (lifecycle.getPendingRunEventAck()) return

      const liveLifecycleMessageId = lifecycle.getMessageId()
      const liveLifecycleMessage = liveLifecycleMessageId
        ? store.entities[liveLifecycleMessageId]
        : undefined
      if (
        liveLifecycleMessage?.roomId === roomId &&
        liveLifecycleMessage.messageType === 'user' &&
        !liveLifecycleMessage.turnTerminalStatus &&
        !allAgentsTerminalForUserMessage(store.entities, roomId, liveLifecycleMessage.id)
      ) {
        if (getToken && reconcileWithDb) {
          void (async () => {
            try {
              const result = await inquiryActiveRuns(
                roomId,
                getToken,
                undefined,
                liveLifecycleMessage.id,
              )
              const backendRunActive = (result.active_runs ?? []).some(
                run => run.trigger_message_id === liveLifecycleMessage.id,
              )
              if (!result.success || backendRunActive) return

              await reconcileWithDb(roomId)
              const refreshedStore = useMessageStore.getState()
              const refreshedMessage = refreshedStore.entities[liveLifecycleMessage.id]
              if (!refreshedMessage?.turnTerminalStatus) return

              const { sending: liveSending } =
                useRoomUiStore.getState().rooms[roomId] ?? {}
              if (
                lifecycle.getMessageId() !== liveLifecycleMessage.id ||
                liveSending ||
                lifecycle.getPendingRunEventAck()
              ) {
                return
              }

              lifecycle.markProcessingResolved()
              refreshedStore.removeMessage(lifecycle.placeholderId(roomId))
              lifecycle.stopProcessing()
            } catch {
              // Preserve the live lifecycle when backend truth cannot be confirmed.
            }
          })()
        }
        return
      }

      if (lifecycle.isPlaceholderDismissed()) return
      store.removeMessage(lifecycle.placeholderId(roomId))
      lifecycle.stopProcessing()
    }
  }, [
    room,
    queryLoading,
    roomId,
    lifecycle,
    hydratedFromDb,
    getToken,
    reconcileWithDb,
  ])
}
