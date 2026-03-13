import { useEffect } from 'react'
import type { MutableRefObject } from 'react'
import type { ProcessingLifecycle } from './processing-lifecycle'
import { useMessageStore } from '@/stores/message-store'
import { streamingBuffer } from '@/stores/streaming-buffer'
import { typewriterManager } from './sse-handlers'

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

    // Clear streaming buffer on room switch
    streamingBuffer.clear()
    typewriterManager.finishAll()

    // Reset UI flags so the new room starts with clean state.
    setSending(false)
    setCancelling(false)
    setSseConnected(false)
    setSseError(null)

    // Initialize normalized store for this room
    useMessageStore.getState().setRoom(roomId)
  }, [roomId, lifecycle, hitlRequestIndex, setSending, setCancelling, setSseConnected, setSseError, resetAgentNameCache])
}
