import { useEffect } from 'react'
import type { ProcessingLifecycle } from './processing-lifecycle'
import { useMessageStore } from '@/stores/message-store'
import { useRoomUiStore } from '@/stores/room-ui-store'
import { TASK_STATE, isTerminalState } from '@/lib/types/sse'
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

    // Once SSE has dismissed the placeholder (via task_submitted or processing_status done),
    // never re-add it — the restore effect is only for page-load recovery.
    if (lifecycle.isPlaceholderDismissed()) return

    const activeRuns = room.active_runs ?? []
    const activeRunTriggerMessageId =
      activeRuns.find((run) => !!run.trigger_message_id)?.trigger_message_id ?? null
    const lifecycleMessageId = activeRunTriggerMessageId
    const hasActiveLifecycle = activeRuns.length > 0

    // Check if room has an active processing state
    if (hasActiveLifecycle) {
      console.log('🔄 Restoring processing placeholder for message:', lifecycleMessageId)

      // Always restore the message ID so cancellation works after refresh,
      // regardless of whether the placeholder is shown below.
      lifecycle.setMessageId(lifecycleMessageId)

      // Some active runs are proactive and may not be anchored to a user message.
      if (!lifecycleMessageId) {
        lifecycle.startProcessing(null)
        return
      }

      // Check if the triggering user message is stale (> 2 min).
      const PLACEHOLDER_STALE_MS = 2 * 60 * 1000
      const triggerMsg = store.entities[lifecycleMessageId]
      if (!triggerMsg) {
        console.log('🔄 Skipping placeholder - processing trigger message not in hydrated store')
        return
      }
      if (isStale(triggerMsg.timestamp, PLACEHOLDER_STALE_MS)) {
        console.log('🔄 Skipping placeholder - processing message is stale (>2min)')
        return
      }

      // Check if any active (non-terminal) task entities already exist in the store
      const hasTaskEntities = Object.values(store.entities).some(
        e => e.roomId === roomId && e.taskStatus && !isTerminalState(e.taskStatus)
      )

      if (hasTaskEntities) {
        console.log('🔄 Skipping placeholder - tasks already exist')
        return
      }

      // Check if placeholder already exists
      const placeholderId = lifecycle.placeholderId(roomId)
      if (store.entities[placeholderId]) return

      // Restore placeholder via normalized store
      store.upsertMessage({
        id: placeholderId,
        roomId,
        messageType: 'agent',
        content: '',
        senderName: 'HYBRO AI',
        taskStatus: TASK_STATE.WORKING,
        taskContent: 'Processing your request\u2026',
        timestamp: new Date().toISOString(),
        isEphemeral: true,
      }, 'optimistic')

      lifecycle.startProcessing(lifecycleMessageId)
    } else {
      // Backend truth says no active processing — aggressively clean up any
      // stale local spinner/placeholder left behind by missed terminal SSE.
      // But don't wipe it if a message send is still in flight (the SSE events
      // haven't arrived yet).
      const { sending } = useRoomUiStore.getState().rooms[roomId] ?? {}
      if (sending) return
      store.removeMessage(lifecycle.placeholderId(roomId))
      lifecycle.stopProcessing()
    }
  }, [room, queryLoading, roomId, lifecycle, hydratedFromDb])
}
