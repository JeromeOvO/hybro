import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, renderHook, waitFor } from '@testing-library/react'
import { useProcessingRestore } from '@/hooks/room/useProcessingRestore'
import type { ProcessingLifecycle } from '@/hooks/room/processing-lifecycle'
import { useMessageStore } from '@/stores/message-store'
import { useRoomUiStore } from '@/stores/room-ui-store'

function createLifecycle({
  placeholderDismissed,
  processingResolved,
}: {
  placeholderDismissed: boolean
  processingResolved: boolean
}): ProcessingLifecycle {
  return {
    setProcessing: vi.fn(),
    startProcessing: vi.fn(),
    stopProcessing: vi.fn(),
    setPendingRunEventAck: vi.fn(),
    getPendingRunEventAck: vi.fn(() => null),
    clearPendingRunEventAck: vi.fn(),
    setSendGuard: vi.fn(),
    isSendGuardActive: vi.fn(() => false),
    setMessageId: vi.fn(),
    getMessageId: vi.fn(() => null),
    dismissPlaceholder: vi.fn(),
    resetPlaceholder: vi.fn(),
    isPlaceholderDismissed: vi.fn(() => placeholderDismissed),
    markProcessingResolved: vi.fn(),
    resetProcessingResolved: vi.fn(),
    isProcessingResolved: vi.fn(() => processingResolved),
    placeholderId: vi.fn((roomId: string) => `processing-placeholder-${roomId}`),
    armCancelTimeout: vi.fn(),
    disarmCancelTimeout: vi.fn(),
    hasCancelTimedOut: vi.fn(() => false),
    setCancelTimedOut: vi.fn(),
    markSseDisconnection: vi.fn(),
    clearSseDisconnection: vi.fn(),
    hadSseDisconnection: vi.fn(() => false),
    reset: vi.fn(),
    dispose: vi.fn(),
  }
}

describe('useProcessingRestore', () => {
  beforeEach(() => {
    useMessageStore.getState().clearRoom()
    useMessageStore.getState().setRoom('room-1')
    useMessageStore.getState().markDbSynced()
    useRoomUiStore.getState().resetAll()
    useMessageStore.getState().upsertMessage({
      id: 'msg-active',
      roomId: 'room-1',
      messageType: 'user',
      content: 'Continue work',
      senderName: 'User',
      timestamp: new Date().toISOString(),
    }, 'db')
  })

  afterEach(() => {
    cleanup()
  })

  it('restores the initial processing log when placeholder was dismissed but processing is not resolved', async () => {
    const lifecycle = createLifecycle({
      placeholderDismissed: true,
      processingResolved: false,
    })

    renderHook(() => useProcessingRestore(
      'room-1',
      { active_runs: [{ trigger_message_id: 'msg-active' }] },
      false,
      lifecycle,
      undefined,
    ))

    await waitFor(() => {
      expect(useMessageStore.getState().entities['msg-active'].processingStatusLogs?.map((entry) => entry.message)).toEqual([
        'Thinking...',
      ])
    })
    expect(lifecycle.startProcessing).toHaveBeenCalledWith('msg-active')
  })
})
