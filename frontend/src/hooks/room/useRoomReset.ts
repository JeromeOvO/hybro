import { useEffect } from 'react'
import type { MutableRefObject } from 'react'
import type { ProcessingLifecycle } from './processing-lifecycle'
import { useMessageStore } from '@/stores/message-store'
import { useStreamingStore } from '@/stores/streaming-store'
import { useRoomUiStore } from '@/stores/room-ui-store'
import { useTurnStore } from '@/stores/turn-store'
import { useTurnPresentationStore } from '@/stores/turn-presentation-store'

export function useRoomReset(
  conversationId: string,
  lifecycle: ProcessingLifecycle,
  hitlRequestIndex: MutableRefObject<Map<string, string>>,
  resetAgentNameCache: () => void,
  setSending: (v: boolean) => void,
  setCancelling: (v: boolean) => void,
  setSseConnected: (v: boolean) => void,
  setSseError: (v: string | null) => void,
) {
  useEffect(() => {
    resetAgentNameCache()
    lifecycle.reset()
    hitlRequestIndex.current.clear()

    // Reset UI flags so the new room starts with clean state.
    setSending(false)
    setCancelling(false)
    setSseConnected(false)
    setSseError(null)

    // Initialize normalized store for this room.
    useMessageStore.getState().setRoom(conversationId)

    // Clear stale buffers from any prior visit to this room. This is distinct
    // from the cleanup below: the cleanup (which runs with the old conversationId in
    // closure) clears buffers when leaving a room; this call clears buffers
    // that survived a previous visit to the room now being entered.
    useStreamingStore.getState().clearRoom(conversationId)

    return () => {
      // Clean up per-room state when leaving this room. The conversationId captured
      // here is the room being left (React closes over the value at the time
      // this effect registered), so both calls target the correct room.
      useRoomUiStore.getState().resetRoom(conversationId)
      useStreamingStore.getState().clearRoom(conversationId)
      useTurnStore.getState().clearRoom(conversationId)
      useTurnPresentationStore.getState().clear()
    }
  }, [conversationId, lifecycle, hitlRequestIndex, setSending, setCancelling, setSseConnected, setSseError, resetAgentNameCache])
}
