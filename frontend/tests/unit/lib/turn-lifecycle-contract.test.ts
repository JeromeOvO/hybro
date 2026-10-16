import { describe, expect, it } from 'vitest'

import { validateCanonicalRunEventData } from '@/lib/turn-lifecycle/contract'

function runStarted(mode: string) {
  return {
    event_id: 'event-1',
    run_id: 'run-1',
    seq: 1,
    type: 'run_started',
    correlation_id: 'cr-1',
    room_seq: 1,
    payload: {
      hybro_turn_id: 'run-1',
      user_message_id: 'msg-1',
      started_at: '2026-10-14T00:00:00Z',
      mode,
    },
  }
}

describe('run_started mode', () => {
  it.each(['fast', 'direct', 'ultimate', 'supervisor', 'passthrough'])(
    'accepts %s',
    (mode) => {
      const result = validateCanonicalRunEventData(runStarted(mode))
      expect(result).toMatchObject({ canonical: true, valid: true })
    },
  )

  it('rejects an unknown mode', () => {
    const result = validateCanonicalRunEventData(runStarted('autonomous'))
    expect(result).toMatchObject({ canonical: true, valid: false })
  })
})
