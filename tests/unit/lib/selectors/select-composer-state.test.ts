import { describe, it, expect, beforeEach } from 'vitest'
import { selectComposerState } from '@/lib/selectors/select-composer-state'
import { useMessageStore } from '@/stores/message-store'
import { createAgentMessage, createUserMessage, resetCounters } from '../../../fixtures'
import { TASK_STATE } from '@/lib/types/sse'

function setup(msgs: ReturnType<typeof createAgentMessage>[]) {
  const store = useMessageStore.getState()
  store.clearRoom()
  store.setRoom('room-1')
  for (const m of msgs) store.upsertMessage(m, 'db')
  const s = useMessageStore.getState()
  return { entities: s.entities, orderedIds: s.orderedIds }
}

describe('selectComposerState', () => {
  beforeEach(() => {
    useMessageStore.getState().clearRoom()
    resetCounters()
  })

  it('returns normal mode with no processing when idle', () => {
    const { entities, orderedIds } = setup([
      createUserMessage({ id: 'u1', roomId: 'room-1' }),
      createAgentMessage({ id: 'a1', roomId: 'room-1', taskStatus: TASK_STATE.COMPLETED }),
    ])
    const result = selectComposerState('room-1', entities, orderedIds)
    expect(result.mode).toBe('normal')
    expect(result.isProcessing).toBe(false)
    expect(result.pendingHitls).toHaveLength(0)
  })

  it('returns isProcessing=true when agent is working', () => {
    const { entities, orderedIds } = setup([
      createAgentMessage({ id: 'a1', roomId: 'room-1', taskStatus: TASK_STATE.WORKING }),
    ])
    const result = selectComposerState('room-1', entities, orderedIds)
    expect(result.isProcessing).toBe(true)
  })

  it('excludes ephemeral from processing check', () => {
    const { entities, orderedIds } = setup([
      createAgentMessage({ id: 'a1', roomId: 'room-1', taskStatus: TASK_STATE.WORKING, isEphemeral: true }),
    ])
    const result = selectComposerState('room-1', entities, orderedIds)
    expect(result.isProcessing).toBe(false)
  })

  it('returns hitl_responding mode when HITL is pending', () => {
    const { entities, orderedIds } = setup([
      createAgentMessage({
        id: 'a1', roomId: 'room-1',
        hitlRequestId: 'req-1', hitlPrompt: 'Q?',
        senderName: 'Agent', taskStatus: TASK_STATE.INPUT_REQUIRED,
      }),
    ])
    const result = selectComposerState('room-1', entities, orderedIds)
    expect(result.mode).toBe('hitl_responding')
    expect(result.pendingHitls).toHaveLength(1)
  })

  it('input-required is NOT counted as processing', () => {
    const { entities, orderedIds } = setup([
      createAgentMessage({
        id: 'a1', roomId: 'room-1',
        taskStatus: TASK_STATE.INPUT_REQUIRED,
        hitlRequestId: 'req-1', hitlPrompt: 'Q?', senderName: 'Agent',
      }),
    ])
    const result = selectComposerState('room-1', entities, orderedIds)
    expect(result.isProcessing).toBe(false)
  })

  it('excludes agents from other rooms', () => {
    const { entities, orderedIds } = setup([
      createAgentMessage({ id: 'a1', roomId: 'room-2', taskStatus: TASK_STATE.WORKING }),
    ])
    const result = selectComposerState('room-1', entities, orderedIds)
    expect(result.isProcessing).toBe(false)
  })
})
