import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { OrchestraTurn } from '@/components/turn/OrchestraTurn'
import { TurnEventLog } from '@/stores/turn-event-store/event-log'
import type { TurnEvent, UserInputData } from '@/stores/turn-event-store/types'

const userInput: UserInputData = { text: 'What is AI?', attachments: [] }

describe('OrchestraTurn', () => {
  it('renders user input and content slots', () => {
    const log = new TurnEventLog('turn-1')
    log.append({
      eventId: 'e1', turnId: 'turn-1', seq: 1, ts: 1000,
      type: 'turn_started', userInput,
    })
    log.append({
      eventId: 'e2', turnId: 'turn-1', seq: 2, ts: 2000,
      type: 'slot_opened', slotId: 'msg-1', slotType: 'agent', agentName: 'Agent A',
    } as TurnEvent)
    log.append({
      eventId: 'e3', turnId: 'turn-1', seq: 3, ts: 3000,
      type: 'slot_terminated', slotId: 'msg-1', status: 'completed',
    } as TurnEvent)

    render(<OrchestraTurn turnLog={log} />)
    expect(screen.getByText('What is AI?')).toBeDefined()
    expect(screen.getByText('Agent A')).toBeDefined()
  })

  it('renders rail items', () => {
    const log = new TurnEventLog('turn-1')
    log.append({
      eventId: 'e1', turnId: 'turn-1', seq: 1, ts: 1000,
      type: 'turn_started', userInput,
    })
    log.append({
      eventId: 'e2', turnId: 'turn-1', seq: 2, ts: 2000,
      type: 'turn_completed', durationMs: 1500,
    } as TurnEvent)

    render(<OrchestraTurn turnLog={log} />)
    expect(screen.getByText('Completed (1.5s)')).toBeDefined()
  })
})
