import { beforeEach, describe, expect, it } from 'vitest'
import { createProcessingLifecycle } from '@/hooks/room/processing-lifecycle'
import { stampLiveTurnTerminalIfInferable } from '@/lib/room-timeline/stamp-live-turn-terminal'
import { useMessageStore } from '@/stores/message-store'
import type { MessageEntity } from '@/stores/message-store/types'

function makeUser(id: string, clientRequestId: string): MessageEntity {
  return {
    id,
    roomId: 'room-1',
    messageType: 'user',
    content: 'hello @a @b',
    senderName: 'User',
    timestamp: '2026-01-01T00:00:00.000Z',
    clientRequestId,
    processingStatusLogs: [{ id: 'log-1', message: 'Thinking...', timestamp: '2026-01-01T00:00:01.000Z' }],
  }
}

function makeAgent(
  id: string,
  relatedMessageId: string,
  clientRequestId: string,
  agentId: string,
): MessageEntity {
  return {
    id,
    roomId: 'room-1',
    messageType: 'agent',
    content: `Answer from ${agentId}`,
    senderName: agentId,
    agentId,
    relatedMessageId,
    clientRequestId,
    taskStatus: 'completed',
    timestamp: '2026-01-01T00:00:02.000Z',
  }
}

describe('stampLiveTurnTerminalIfInferable', () => {
  beforeEach(() => {
    useMessageStore.getState().clearRoom()
    useMessageStore.getState().setRoom('room-1')
  })

  it('does not stamp when all agents finish but turn is not officially terminal', () => {
    const lifecycle = createProcessingLifecycle(() => {})
    lifecycle.startProcessing('u1')
    lifecycle.setPendingRunEventAck('cr-1')

    useMessageStore.getState().upsertMany([
      makeUser('u1', 'cr-1'),
      makeAgent('a1', 'u1', 'cr-1', 'agent-a'),
      makeAgent('a2', 'u1', 'cr-1', 'agent-b'),
    ], 'sse')

    expect(stampLiveTurnTerminalIfInferable('room-1', lifecycle, {
      clientRequestId: 'cr-1',
      relatedMessageId: 'u1',
    })).toBe(false)
    expect(useMessageStore.getState().entities.u1?.turnTerminalStatus).toBeUndefined()
    expect(lifecycle.isProcessingResolved()).toBe(false)
  })

  it('stamps turnTerminalStatus and clears lifecycle when deterministic summary arrives', () => {
    const lifecycle = createProcessingLifecycle(() => {})
    lifecycle.startProcessing('u1')
    lifecycle.setPendingRunEventAck('cr-1')

    useMessageStore.getState().upsertMany([
      makeUser('u1', 'cr-1'),
      makeAgent('a1', 'u1', 'cr-1', 'agent-a'),
      makeAgent('a2', 'u1', 'cr-1', 'agent-b'),
      {
        ...makeAgent('summary-u1', 'u1', 'cr-1', 'summary'),
        content: '2 agents responded. Expand below to read each answer.',
        summaryOrigin: 'deterministic',
      },
    ], 'sse')

    const stamped = stampLiveTurnTerminalIfInferable('room-1', lifecycle, {
      clientRequestId: 'cr-1',
      relatedMessageId: 'u1',
    })

    expect(stamped).toBe(true)
    expect(useMessageStore.getState().entities.u1?.turnTerminalStatus).toBe('completed')
    expect(lifecycle.isProcessingResolved()).toBe(true)
    expect(lifecycle.isSendGuardActive()).toBe(false)
  })

  it('does not stamp while an agent is still working', () => {
    const lifecycle = createProcessingLifecycle(() => {})
    lifecycle.startProcessing('u1')

    useMessageStore.getState().upsertMany([
      makeUser('u1', 'cr-1'),
      makeAgent('a1', 'u1', 'cr-1', 'agent-a'),
      {
        ...makeAgent('a2', 'u1', 'cr-1', 'agent-b'),
        taskStatus: 'working',
        content: '',
      },
    ], 'sse')

    expect(stampLiveTurnTerminalIfInferable('room-1', lifecycle, {
      clientRequestId: 'cr-1',
      relatedMessageId: 'u1',
    })).toBe(false)
    expect(useMessageStore.getState().entities.u1?.turnTerminalStatus).toBeUndefined()
  })
})
