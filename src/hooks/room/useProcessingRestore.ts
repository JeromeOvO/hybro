import { useEffect } from 'react'
import type { ProcessingLifecycle } from './processing-lifecycle'
import { useMessageStore } from '@/stores/message-store'
import { TASK_STATE, isTerminalState } from '@/lib/types/sse'
import { isStale } from '@/lib/time'

export function useProcessingRestore(
  roomId: string,
  room: { processing_message_id?: string | null } | null,
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

    // Check if room has an active processing state
    if (room.processing_message_id) {
      console.log('🔄 Restoring processing placeholder for message:', room.processing_message_id)

      // Always restore the message ID so cancellation works after refresh,
      // regardless of whether the placeholder is shown below.
      lifecycle.setMessageId(room.processing_message_id)

      // Check if the triggering user message is stale (> 2 min).
      const PLACEHOLDER_STALE_MS = 2 * 60 * 1000
      const triggerMsg = store.entities[room.processing_message_id]
      if (triggerMsg && isStale(triggerMsg.timestamp, PLACEHOLDER_STALE_MS)) {
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
        taskContent: 'Processing your request...',
        timestamp: new Date().toISOString(),
        isEphemeral: true,
      }, 'optimistic')

      lifecycle.setProcessing(true)
    }
  }, [room, queryLoading, roomId, lifecycle, hydratedFromDb])
}
