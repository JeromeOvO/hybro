import { describe, expect, it, beforeEach } from 'vitest'
import { handleArtifactUpdate } from '@/hooks/room/sse-handlers/handlers/artifact-update'
import { useStreamingStore } from '@/stores/streaming-store'
import type { ProcessingLifecycle } from '@/hooks/room/processing-lifecycle'

function makeLifecycle(): ProcessingLifecycle {
  return {
    placeholderId: () => 'placeholder',
    isPlaceholderDismissed: () => true,
    dismissPlaceholder: () => {},
    disarmCancelTimeout: () => {},
    hasCancelTimedOut: () => false,
  } as ProcessingLifecycle
}

describe('handleArtifactUpdate', () => {
  beforeEach(() => {
    useStreamingStore.setState({ buffers: {} })
  })

  it('defaults append to true when the same artifact id already exists in the buffer', () => {
    useStreamingStore.getState().append('msg-1', 'room-1', {
      artifactId: 'stream-1',
      parts: [{ kind: 'text', text: 'Earlier paragraph. ' }],
    }, false)

    handleArtifactUpdate(
      { roomId: 'room-1', lifecycle: makeLifecycle() },
      {
        type: 'artifact_update',
        room_id: 'room-1',
        timestamp: new Date().toISOString(),
        data: {
          message_id: 'msg-1',
          agent_id: 'agent-1',
          client_request_id: 'req-1',
          artifact: {
            artifact_id: 'stream-1',
            parts: [{ kind: 'text', text: 'Later paragraph.' }],
          },
          append: undefined as unknown as boolean,
          last_chunk: false,
        },
      },
      { shouldDrop: false, shouldBuffer: false, clientReqId: 'req-1' },
    )

    expect(useStreamingStore.getState().buffers['msg-1']?.text).toBe(
      'Earlier paragraph. Later paragraph.',
    )
  })
})
