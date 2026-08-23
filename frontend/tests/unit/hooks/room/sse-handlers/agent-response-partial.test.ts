import { describe, expect, it, beforeEach } from 'vitest'
import { handleAgentResponsePartial } from '@/hooks/room/sse-handlers/handlers/agent-response'
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
  } as unknown as ProcessingLifecycle
}

describe('handleAgentResponsePartial', () => {
  beforeEach(() => {
    useStreamingStore.setState({ buffers: {} })
  })

  it('appends partial deltas to a message-scoped buffer keyed by message_id', () => {
    const ctx = { roomId: 'room-1', lifecycle: makeLifecycle() } as Parameters<typeof handleAgentResponsePartial>[0]

    handleAgentResponsePartial(
      ctx,
      {
        type: 'agent_response_partial',
        room_id: 'room-1',
        timestamp: new Date().toISOString(),
        data: {
          message_id: 'agent-1',
          agent_id: 'agent-a',
          content_delta: 'Hello',
          client_request_id: 'req-1',
        },
      },
      'req-1',
    )

    handleAgentResponsePartial(
      ctx,
      {
        type: 'agent_response_partial',
        room_id: 'room-1',
        timestamp: new Date().toISOString(),
        data: {
          message_id: 'agent-1',
          agent_id: 'agent-a',
          content_delta: ' world',
          client_request_id: 'req-1',
        },
      },
      'req-1',
    )

    expect(useStreamingStore.getState().buffers['agent-1']?.text).toBe('Hello world')
    expect(useStreamingStore.getState().buffers['req-1']).toBeUndefined()
  })

  it('keeps parallel agent buffers isolated under the same client_request_id', () => {
    const ctx = { roomId: 'room-1', lifecycle: makeLifecycle() } as Parameters<typeof handleAgentResponsePartial>[0]

    handleAgentResponsePartial(
      ctx,
      {
        type: 'agent_response_partial',
        room_id: 'room-1',
        timestamp: new Date().toISOString(),
        data: {
          message_id: 'hermes-1',
          agent_id: 'hermes',
          content_delta: 'Hermes text',
          client_request_id: 'req-turn',
        },
      },
      'req-turn',
    )

    handleAgentResponsePartial(
      ctx,
      {
        type: 'agent_response_partial',
        room_id: 'room-1',
        timestamp: new Date().toISOString(),
        data: {
          message_id: 'codex-1',
          agent_id: 'codex',
          content_delta: 'Codex text',
          client_request_id: 'req-turn',
        },
      },
      'req-turn',
    )

    expect(useStreamingStore.getState().buffers['hermes-1']?.text).toBe('Hermes text')
    expect(useStreamingStore.getState().buffers['codex-1']?.text).toBe('Codex text')
  })

  it('matches terminal artifact_update concat semantics for multi-chunk text', () => {
    const lifecycle = makeLifecycle()
    const artifactCtx = { roomId: 'room-1', lifecycle }
    const partialCtx = { roomId: 'room-1', lifecycle } as Parameters<typeof handleAgentResponsePartial>[0]

    handleArtifactUpdate(
      artifactCtx,
      {
        type: 'artifact_update',
        room_id: 'room-1',
        timestamp: new Date().toISOString(),
        data: {
          message_id: 'msg-artifact',
          agent_id: 'agent-1',
          client_request_id: 'req-1',
          artifact: {
            artifact_id: 'stream-1',
            name: 'response',
            parts: [{ kind: 'text', text: 'Line one. ' }],
          },
          append: false,
          last_chunk: true,
        },
      },
      'req-1',
    )

    handleArtifactUpdate(
      artifactCtx,
      {
        type: 'artifact_update',
        room_id: 'room-1',
        timestamp: new Date().toISOString(),
        data: {
          message_id: 'msg-artifact',
          agent_id: 'agent-1',
          client_request_id: 'req-1',
          artifact: {
            artifact_id: 'stream-2',
            name: 'response',
            parts: [{ kind: 'text', text: 'Line two.' }],
          },
          append: false,
          last_chunk: true,
        },
      },
      'req-1',
    )

    const artifactText = useStreamingStore.getState().buffers['msg-artifact']?.text
    useStreamingStore.setState({ buffers: {} })

    handleAgentResponsePartial(
      partialCtx,
      {
        type: 'agent_response_partial',
        room_id: 'room-1',
        timestamp: new Date().toISOString(),
        data: {
          message_id: 'msg-partial',
          agent_id: 'agent-1',
          content_delta: 'Line one. ',
          client_request_id: 'req-1',
        },
      },
      'req-1',
    )

    handleAgentResponsePartial(
      partialCtx,
      {
        type: 'agent_response_partial',
        room_id: 'room-1',
        timestamp: new Date().toISOString(),
        data: {
          message_id: 'msg-partial',
          agent_id: 'agent-1',
          content_delta: 'Line two.',
          client_request_id: 'req-1',
        },
      },
      'req-1',
    )

    const partialText = useStreamingStore.getState().buffers['msg-partial']?.text
    expect(artifactText).toBe('Line one. Line two.')
    expect(partialText).toBe('Line one. Line two.')
  })
})
