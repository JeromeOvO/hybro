import { describe, expect, it, beforeEach } from 'vitest'
import { applyRoomCommands } from '@/hooks/room/sse-handlers/apply-commands'
import { useMessageStore } from '@/stores/message-store'
import { useStreamingStore } from '@/stores/streaming-store'
import { createUserMessage, resetCounters } from '../../../../fixtures'

describe('applyRoomCommands', () => {
  beforeEach(() => {
    resetCounters()
    useMessageStore.getState().clearRoom()
    useStreamingStore.setState({ buffers: {} })
  })

  it('applies upsert and stream_clear in order', () => {
    useMessageStore.getState().setRoom('room-1')
    useStreamingStore.getState().append('msg-1', 'room-1', {
      artifactId: 'a1',
      parts: [{ kind: 'text', text: 'live' }],
    }, false)

    applyRoomCommands([
      {
        type: 'upsert_message',
        source: 'sse',
        message: createUserMessage({ id: 'msg-1', roomId: 'room-1', content: 'final' }),
      },
      { type: 'stream_clear', messageId: 'msg-1' },
    ])

    expect(useMessageStore.getState().entities['msg-1']?.content).toBe('final')
    expect(useStreamingStore.getState().buffers['msg-1']).toBeUndefined()
  })
})
