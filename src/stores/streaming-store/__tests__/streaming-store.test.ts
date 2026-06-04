import { describe, it, expect, beforeEach, vi } from 'vitest'
import { useStreamingStore } from '../index'
import type { ArtifactData } from '@/stores/message-store/types'

// ── Helpers ───────────────────────────────────────────────────

function makeChunk(text: string): ArtifactData {
  return {
    artifactId: 'art-1',
    name: 'output',
    parts: [{ kind: 'text', text }],
  }
}

beforeEach(() => {
  // Reset the Zustand store between tests by clearing all buffers.
  useStreamingStore.setState({ buffers: {} })
})

// ── append — basic behaviour ──────────────────────────────────

describe('append', () => {
  it('creates a new buffer on first append', () => {
    useStreamingStore.getState().append('msg-1', 'room-1', makeChunk('Hello'), false)
    const buf = useStreamingStore.getState().buffers['msg-1']
    expect(buf).toBeDefined()
    expect(buf.text).toBe('Hello')
    expect(buf.roomId).toBe('room-1')
    expect(buf.isComplete).toBe(false)
    expect(buf.lastUpdatedAt).toBeGreaterThan(0)
  })

  it('accumulates text across multiple appends', () => {
    useStreamingStore.getState().append('msg-1', 'room-1', makeChunk('Hello'), false)
    useStreamingStore.getState().append('msg-1', 'room-1', makeChunk(' world'), true)
    const buf = useStreamingStore.getState().buffers['msg-1']
    expect(buf.text).toBe('Hello world')
  })

  it('stamps roomId from the first append and preserves it on re-append', () => {
    useStreamingStore.getState().append('msg-1', 'room-A', makeChunk('chunk'), false)
    useStreamingStore.getState().append('msg-1', 'room-A', makeChunk(' more'), true)
    expect(useStreamingStore.getState().buffers['msg-1'].roomId).toBe('room-A')
  })

  it('updates lastUpdatedAt on each append', () => {
    vi.useFakeTimers()
    vi.setSystemTime(1_000)
    useStreamingStore.getState().append('msg-1', 'room-1', makeChunk('first'), false)
    const t1 = useStreamingStore.getState().buffers['msg-1'].lastUpdatedAt

    vi.setSystemTime(2_000)
    useStreamingStore.getState().append('msg-1', 'room-1', makeChunk('second'), true)
    const t2 = useStreamingStore.getState().buffers['msg-1'].lastUpdatedAt

    expect(t2).toBeGreaterThan(t1)
    vi.useRealTimers()
  })

  it('preserves isComplete flag when appending to an already-complete buffer', () => {
    useStreamingStore.getState().append('msg-1', 'room-1', makeChunk('text'), false)
    useStreamingStore.getState().markComplete('msg-1')
    useStreamingStore.getState().append('msg-1', 'room-1', makeChunk(' more'), true)
    // isComplete is carried from the existing buffer
    expect(useStreamingStore.getState().buffers['msg-1'].isComplete).toBe(true)
  })
})

// ── append — stale eviction ───────────────────────────────────

describe('append — stale buffer eviction', () => {
  it('evicts a buffer idle for more than 5 minutes on the next append for a different message', () => {
    vi.useFakeTimers()

    vi.setSystemTime(1_000)
    useStreamingStore.getState().append('stale-msg', 'room-1', makeChunk('old'), false)

    // Advance past the 5-minute TTL
    vi.setSystemTime(1_000 + 5 * 60_000 + 1)
    // Append for a different message ID triggers the sweep
    useStreamingStore.getState().append('fresh-msg', 'room-1', makeChunk('new'), false)

    const buffers = useStreamingStore.getState().buffers
    expect(buffers['stale-msg']).toBeUndefined()
    expect(buffers['fresh-msg']).toBeDefined()

    vi.useRealTimers()
  })

  it('does NOT evict the buffer currently being appended, even if its timestamp is old', () => {
    vi.useFakeTimers()

    vi.setSystemTime(1_000)
    useStreamingStore.getState().append('msg-1', 'room-1', makeChunk('original'), false)

    // Advance past TTL
    vi.setSystemTime(1_000 + 5 * 60_000 + 1)
    // Append to the same message — must not self-evict
    useStreamingStore.getState().append('msg-1', 'room-1', makeChunk(' more'), true)

    expect(useStreamingStore.getState().buffers['msg-1']).toBeDefined()
    expect(useStreamingStore.getState().buffers['msg-1'].text).toBe('original more')

    vi.useRealTimers()
  })

  it('keeps a fresh buffer (< 5 min) when another message triggers the sweep', () => {
    vi.useFakeTimers()

    vi.setSystemTime(1_000)
    useStreamingStore.getState().append('msg-1', 'room-1', makeChunk('alive'), false)

    vi.setSystemTime(1_000 + 4 * 60_000) // 4 minutes — under TTL
    useStreamingStore.getState().append('msg-2', 'room-1', makeChunk('trigger'), false)

    expect(useStreamingStore.getState().buffers['msg-1']).toBeDefined()

    vi.useRealTimers()
  })

  it('evicts multiple stale buffers in one append call', () => {
    vi.useFakeTimers()

    vi.setSystemTime(1_000)
    useStreamingStore.getState().append('stale-1', 'room-1', makeChunk('a'), false)
    useStreamingStore.getState().append('stale-2', 'room-1', makeChunk('b'), false)

    vi.setSystemTime(1_000 + 5 * 60_000 + 1)
    useStreamingStore.getState().append('fresh', 'room-1', makeChunk('c'), false)

    const buffers = useStreamingStore.getState().buffers
    expect(buffers['stale-1']).toBeUndefined()
    expect(buffers['stale-2']).toBeUndefined()
    expect(buffers['fresh']).toBeDefined()

    vi.useRealTimers()
  })
})

// ── markComplete ──────────────────────────────────────────────

describe('markComplete', () => {
  it('sets isComplete to true', () => {
    useStreamingStore.getState().append('msg-1', 'room-1', makeChunk('text'), false)
    useStreamingStore.getState().markComplete('msg-1')
    expect(useStreamingStore.getState().buffers['msg-1'].isComplete).toBe(true)
  })

  it('updates lastUpdatedAt when marking complete', () => {
    vi.useFakeTimers()
    vi.setSystemTime(1_000)
    useStreamingStore.getState().append('msg-1', 'room-1', makeChunk('text'), false)

    vi.setSystemTime(2_000)
    useStreamingStore.getState().markComplete('msg-1')
    expect(useStreamingStore.getState().buffers['msg-1'].lastUpdatedAt).toBe(2_000)
    vi.useRealTimers()
  })

  it('is a no-op when the buffer does not exist', () => {
    useStreamingStore.getState().markComplete('nonexistent')
    expect(useStreamingStore.getState().buffers['nonexistent']).toBeUndefined()
  })

  it('is a no-op when the buffer is already complete', () => {
    useStreamingStore.getState().append('msg-1', 'room-1', makeChunk('text'), false)
    useStreamingStore.getState().markComplete('msg-1')
    const ts1 = useStreamingStore.getState().buffers['msg-1'].lastUpdatedAt

    // Second markComplete should be a no-op (returns same state)
    useStreamingStore.getState().markComplete('msg-1')
    expect(useStreamingStore.getState().buffers['msg-1'].lastUpdatedAt).toBe(ts1)
  })

  it('prevents stale eviction of a completed buffer whose lastUpdatedAt was refreshed', () => {
    vi.useFakeTimers()

    vi.setSystemTime(1_000)
    useStreamingStore.getState().append('msg-1', 'room-1', makeChunk('text'), false)

    vi.setSystemTime(3 * 60_000) // 3 min — markComplete refreshes the timestamp
    useStreamingStore.getState().markComplete('msg-1')

    // Now advance only another 3 minutes past markComplete (total < TTL from markComplete)
    vi.setSystemTime(3 * 60_000 + 3 * 60_000) // 6 min total, but only 3 min since markComplete
    useStreamingStore.getState().append('trigger', 'room-1', makeChunk('x'), false)

    expect(useStreamingStore.getState().buffers['msg-1']).toBeDefined()
    vi.useRealTimers()
  })
})

// ── clear ─────────────────────────────────────────────────────

describe('clear', () => {
  it('removes the buffer for the given message ID', () => {
    useStreamingStore.getState().append('msg-1', 'room-1', makeChunk('text'), false)
    useStreamingStore.getState().clear('msg-1')
    expect(useStreamingStore.getState().buffers['msg-1']).toBeUndefined()
  })

  it('does not remove other buffers', () => {
    useStreamingStore.getState().append('msg-1', 'room-1', makeChunk('a'), false)
    useStreamingStore.getState().append('msg-2', 'room-1', makeChunk('b'), false)
    useStreamingStore.getState().clear('msg-1')
    expect(useStreamingStore.getState().buffers['msg-2']).toBeDefined()
  })

  it('is a no-op when the buffer does not exist', () => {
    useStreamingStore.getState().append('msg-1', 'room-1', makeChunk('text'), false)
    useStreamingStore.getState().clear('nonexistent')
    expect(useStreamingStore.getState().buffers['msg-1']).toBeDefined()
  })
})

// ── clearByClientRequestId ───────────────────────────────────

describe('clearByClientRequestId', () => {
  it('removes all buffers tagged with the client request id', () => {
    useStreamingStore.getState().append('req-1', 'room-1', makeChunk('a'), false, { clientRequestId: 'req-1' })
    useStreamingStore.getState().append('msg-2', 'room-1', makeChunk('b'), false, { clientRequestId: 'req-1' })
    useStreamingStore.getState().append('msg-3', 'room-1', makeChunk('c'), false, { clientRequestId: 'req-3' })

    useStreamingStore.getState().clearByClientRequestId('req-1')

    expect(useStreamingStore.getState().buffers['req-1']).toBeUndefined()
    expect(useStreamingStore.getState().buffers['msg-2']).toBeUndefined()
    expect(useStreamingStore.getState().buffers['msg-3']).toBeDefined()
  })
})

// ── clearRoom ─────────────────────────────────────────────────

describe('clearRoom', () => {
  it('clears all buffers belonging to the specified roomId', () => {
    useStreamingStore.getState().append('msg-1', 'room-A', makeChunk('a'), false)
    useStreamingStore.getState().append('msg-2', 'room-A', makeChunk('b'), false)
    useStreamingStore.getState().clearRoom('room-A')
    expect(useStreamingStore.getState().buffers['msg-1']).toBeUndefined()
    expect(useStreamingStore.getState().buffers['msg-2']).toBeUndefined()
  })

  it('does NOT clear buffers belonging to a different room', () => {
    useStreamingStore.getState().append('msg-a', 'room-A', makeChunk('a'), false)
    useStreamingStore.getState().append('msg-b', 'room-B', makeChunk('b'), false)
    useStreamingStore.getState().clearRoom('room-A')
    expect(useStreamingStore.getState().buffers['msg-a']).toBeUndefined()
    expect(useStreamingStore.getState().buffers['msg-b']).toBeDefined()
  })

  it('is a no-op when no buffers belong to the room', () => {
    useStreamingStore.getState().append('msg-1', 'room-X', makeChunk('x'), false)
    useStreamingStore.getState().clearRoom('room-Y') // different room
    expect(useStreamingStore.getState().buffers['msg-1']).toBeDefined()
  })

  it('returns the same state reference when nothing changed (no spurious re-renders)', () => {
    useStreamingStore.getState().append('msg-1', 'room-X', makeChunk('x'), false)
    const before = useStreamingStore.getState().buffers
    useStreamingStore.getState().clearRoom('room-Z') // no matching buffers
    const after = useStreamingStore.getState().buffers
    expect(after).toBe(before) // same reference — Zustand skips re-render
  })
})

// ── clearByMessageIds ─────────────────────────────────────────

describe('clearByMessageIds', () => {
  it('clears only the buffers whose IDs are in the set', () => {
    useStreamingStore.getState().append('msg-1', 'room-1', makeChunk('a'), false)
    useStreamingStore.getState().append('msg-2', 'room-1', makeChunk('b'), false)
    useStreamingStore.getState().append('msg-3', 'room-1', makeChunk('c'), false)
    useStreamingStore.getState().clearByMessageIds(new Set(['msg-1', 'msg-3']))
    expect(useStreamingStore.getState().buffers['msg-1']).toBeUndefined()
    expect(useStreamingStore.getState().buffers['msg-2']).toBeDefined()
    expect(useStreamingStore.getState().buffers['msg-3']).toBeUndefined()
  })

  it('preserves buffers for actively-streaming messages not in the set', () => {
    useStreamingStore.getState().append('persisted', 'room-1', makeChunk('done'), false)
    useStreamingStore.getState().append('streaming', 'room-1', makeChunk('in-progress'), false)
    // Only the persisted message is in the reconciled DB set
    useStreamingStore.getState().clearByMessageIds(new Set(['persisted']))
    expect(useStreamingStore.getState().buffers['persisted']).toBeUndefined()
    expect(useStreamingStore.getState().buffers['streaming']).toBeDefined()
  })

  it('is a no-op when the set is empty', () => {
    useStreamingStore.getState().append('msg-1', 'room-1', makeChunk('a'), false)
    useStreamingStore.getState().clearByMessageIds(new Set())
    expect(useStreamingStore.getState().buffers['msg-1']).toBeDefined()
  })

  it('does not care about room — it clears by message ID regardless of room', () => {
    useStreamingStore.getState().append('msg-A', 'room-1', makeChunk('a'), false)
    useStreamingStore.getState().append('msg-B', 'room-2', makeChunk('b'), false)
    useStreamingStore.getState().clearByMessageIds(new Set(['msg-A']))
    expect(useStreamingStore.getState().buffers['msg-A']).toBeUndefined()
    expect(useStreamingStore.getState().buffers['msg-B']).toBeDefined()
  })

  it('returns the same state reference when nothing changed (no spurious re-renders)', () => {
    useStreamingStore.getState().append('msg-1', 'room-1', makeChunk('a'), false)
    const before = useStreamingStore.getState().buffers
    useStreamingStore.getState().clearByMessageIds(new Set(['msg-never-existed']))
    const after = useStreamingStore.getState().buffers
    expect(after).toBe(before)
  })
})
