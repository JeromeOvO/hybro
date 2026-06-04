import { useEffect } from 'react'
import type { ProcessingLifecycle } from './processing-lifecycle'
import { ensureInitialProcessingStatusLog } from './processing-status-log'
import { useMessageStore } from '@/stores/message-store'
import { useRoomUiStore } from '@/stores/room-ui-store'
import { allAgentsTerminalForUserMessage } from '@/lib/room-timeline/turn-agent-terminal'
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

    // Check if room has an active processing state
    if (hasActiveLifecycle) {
      // Always restore the message ID so cancellation works after refresh.
      lifecycle.setMessageId(lifecycleMessageId)

      // Stale active_runs after server restart — every agent task is already terminal.
      if (
        lifecycleMessageId
        && allAgentsTerminalForUserMessage(store.entities, roomId, lifecycleMessageId)
      ) {
        console.log('🔄 Skipping processing restore — all agents terminal for message:', lifecycleMessageId)
        const { sending } = useRoomUiStore.getState().rooms[roomId] ?? {}
        if (!sending) {
          store.removeMessage(lifecycle.placeholderId(roomId))
          lifecycle.stopProcessing()
        }
        return
      }

      console.log('🔄 Restoring processing log for message:', lifecycleMessageId)

      // Some active runs are proactive and may not be anchored to a user message.
      if (!lifecycleMessageId) {
        lifecycle.startProcessing(null)
        return
      }

      // Check if the triggering user message is stale (> 2 min).
      const PLACEHOLDER_STALE_MS = 2 * 60 * 1000
      const triggerMsg = store.entities[lifecycleMessageId]
      if (!triggerMsg) {
        console.log('🔄 Skipping processing log - processing trigger message not in hydrated store')
        return
      }
      if (isStale(triggerMsg.timestamp, PLACEHOLDER_STALE_MS)) {
        console.log('🔄 Skipping processing log - processing message is stale (>2min)')
        return
      }

      // Check if any active (non-terminal) task entities already exist in the store
      const hasTaskEntities = Object.values(store.entities).some(
        e => e.roomId === roomId && e.taskStatus && !isTerminalState(e.taskStatus)
      )

      if (hasTaskEntities) {
        console.log('🔄 Skipping processing log - tasks already exist')
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
      if (lifecycle.isPlaceholderDismissed()) return
      const { sending } = useRoomUiStore.getState().rooms[roomId] ?? {}
      if (sending) return
      if (lifecycle.getPendingRunEventAck()) return
      store.removeMessage(lifecycle.placeholderId(roomId))
      lifecycle.stopProcessing()
    }
  }, [room, queryLoading, roomId, lifecycle, hydratedFromDb])
}
