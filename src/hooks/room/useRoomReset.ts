import { useEffect } from 'react'
import type { MutableRefObject } from 'react'
import type { ProcessingLifecycle } from './processing-lifecycle'
import { useMessageStore } from '@/stores/message-store'
import { useStreamingStore } from '@/stores/streaming-store'
import { useRoomUiStore } from '@/stores/room-ui-store'

export function useRoomReset(
  roomId: string,
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
    useMessageStore.getState().setRoom(roomId)

    // Clear any streaming buffers left from the previous room.
    // clearRoom filters by roomId so buffers from other rooms are never touched.
    useStreamingStore.getState().clearRoom(roomId)

    return () => {
      // Clean up per-room UI flags when leaving this room.
      useRoomUiStore.getState().resetRoom(roomId)
    }
  }, [roomId, lifecycle, hitlRequestIndex, setSending, setCancelling, setSseConnected, setSseError, resetAgentNameCache])
}
