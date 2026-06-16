import { describe, it, expect, beforeEach, vi } from 'vitest'
import {
  getResolvedMessageId,
  resolveClientRequestMessageId,
  clearPendingSseForClientRequest,
  enqueuePendingSseEvent,
  flushPendingSseEvents,
} from '../pending-turn-buffer'
import type { SSEMessage } from '@/lib/types/sse'

// ── Helpers ───────────────────────────────────────────────────

function makeEvent(type: SSEMessage['type'] = 'task_update'): SSEMessage {
  return { type, timestamp: new Date().toISOString(), data: {} } as SSEMessage
}

// Each test must use a unique clientRequestId because the module maps are
// module-level singletons. Sharing IDs across tests causes interference.
let idCounter = 0
function uid(): string {
  return `cr-${++idCounter}`
}

// ── Resolution map ────────────────────────────────────────────

describe('resolveClientRequestMessageId / getResolvedMessageId', () => {
  it('returns undefined for an unknown clientRequestId', () => {
    expect(getResolvedMessageId('unknown-cr')).toBeUndefined()
  })

  it('returns the resolved messageId after an explicit resolve call', () => {
    const cr = uid()
    resolveClientRequestMessageId(cr, 'msg-abc')
    expect(getResolvedMessageId(cr)).toBe('msg-abc')
  })

  it('overwrites an existing resolution with a newer messageId', () => {
    const cr = uid()
    resolveClientRequestMessageId(cr, 'msg-1')
    resolveClientRequestMessageId(cr, 'msg-2')
    expect(getResolvedMessageId(cr)).toBe('msg-2')
  })
})

// ── clearPendingSseForClientRequest ───────────────────────────

describe('clearPendingSseForClientRequest', () => {
  it('removes the resolution entry so getResolvedMessageId returns undefined', () => {
    const cr = uid()
    resolveClientRequestMessageId(cr, 'msg-xyz')
    expect(getResolvedMessageId(cr)).toBe('msg-xyz')

    clearPendingSseForClientRequest(cr)
    expect(getResolvedMessageId(cr)).toBeUndefined()
  })

  it('removes buffered events for the clientRequestId', async () => {
    const cr = uid()
    enqueuePendingSseEvent(cr, makeEvent('task_update'))
    enqueuePendingSseEvent(cr, makeEvent('artifact_update'))

    clearPendingSseForClientRequest(cr)

    // After clearing, flush should dispatch nothing
    const dispatched: SSEMessage[] = []
    await flushPendingSseEvents(cr, async (e) => { dispatched.push(e) }, 'msg-any')
    expect(dispatched).toHaveLength(0)
  })

  it('is a no-op for a clientRequestId that was never used', () => {
    expect(() => clearPendingSseForClientRequest('never-used')).not.toThrow()
  })

  it('does not clear other clientRequestIds', () => {
    const cr1 = uid()
    const cr2 = uid()
    resolveClientRequestMessageId(cr1, 'msg-1')
    resolveClientRequestMessageId(cr2, 'msg-2')

    clearPendingSseForClientRequest(cr1)

    expect(getResolvedMessageId(cr1)).toBeUndefined()
    expect(getResolvedMessageId(cr2)).toBe('msg-2')
  })
})

// ── enqueuePendingSseEvent ────────────────────────────────────

describe('enqueuePendingSseEvent', () => {
  it('buffers events and they are dispatched by flushPendingSseEvents', async () => {
    const cr = uid()
    const e1 = makeEvent('task_submitted')
    const e2 = makeEvent('task_update')
    enqueuePendingSseEvent(cr, e1)
    enqueuePendingSseEvent(cr, e2)

    const dispatched: SSEMessage[] = []
    await flushPendingSseEvents(cr, async (e) => { dispatched.push(e) }, 'msg-1')
    expect(dispatched).toEqual([e1, e2])
  })

  it('dispatches buffered events in FIFO order', async () => {
    const cr = uid()
    const events = [
      makeEvent('task_submitted'),
      makeEvent('artifact_update'),
      makeEvent('task_update'),
    ]
    for (const e of events) enqueuePendingSseEvent(cr, e)

    const dispatched: SSEMessage[] = []
    await flushPendingSseEvents(cr, async (e) => { dispatched.push(e) }, 'msg-1')
    expect(dispatched.map(e => e.type)).toEqual(['task_submitted', 'artifact_update', 'task_update'])
  })

  it('returns false and drops the event when per-request cap is exceeded', () => {
    const cr = uid()
    // Fill to cap (256)
    for (let i = 0; i < 256; i++) {
      enqueuePendingSseEvent(cr, makeEvent('artifact_update'))
    }
    const accepted = enqueuePendingSseEvent(cr, makeEvent('task_update'))
    expect(accepted).toBe(false)
  })
})

// ── flushPendingSseEvents ─────────────────────────────────────

describe('flushPendingSseEvents', () => {
  it('resolves the clientRequestId to the provided messageId', async () => {
    const cr = uid()
    await flushPendingSseEvents(cr, async () => {}, 'msg-resolved')
    expect(getResolvedMessageId(cr)).toBe('msg-resolved')
  })

  it('the resolution entry is kept after flush (subsequent SSE events still correlate)', async () => {
    const cr = uid()
    enqueuePendingSseEvent(cr, makeEvent('task_update'))
    await flushPendingSseEvents(cr, async () => {}, 'msg-123')

    // Resolution must survive the flush — a late-arriving SSE for the same
    // clientRequestId should still be dispatched, not re-buffered.
    expect(getResolvedMessageId(cr)).toBe('msg-123')
  })

  it('pending buffer is cleared after flush even when empty', async () => {
    const cr = uid()
    // No events queued — flush still clears the pending slot
    await flushPendingSseEvents(cr, async () => {}, 'msg-empty')
    // Subsequent enqueue should be a fresh buffer (no leftover state)
    const e = makeEvent('task_submitted')
    enqueuePendingSseEvent(cr, e)
    const dispatched: SSEMessage[] = []
    await flushPendingSseEvents(cr, async (ev) => { dispatched.push(ev) }, 'msg-empty')
    expect(dispatched).toEqual([e])
  })

  it('dispatches events sequentially (not in parallel)', async () => {
    const cr = uid()
    const order: number[] = []
    enqueuePendingSseEvent(cr, makeEvent('task_submitted'))
    enqueuePendingSseEvent(cr, makeEvent('task_update'))

    let call = 0
    await flushPendingSseEvents(cr, async () => { order.push(++call) }, 'msg-seq')
    expect(order).toEqual([1, 2])
  })
})

// ── TTL eviction of resolution map ───────────────────────────

describe('resolution map TTL eviction', () => {
  it('evicts a stale resolution entry (> 10 min) on next enqueuePendingSseEvent', () => {
    vi.useFakeTimers()
    const cr = uid()
    // Resolve with a timestamp in the past
    resolveClientRequestMessageId(cr, 'old-msg')
    // Advance past the 10-minute TTL
    vi.advanceTimersByTime(10 * 60_000 + 1)
    // Enqueue an event for a different ID to trigger the sweep
    const otherCr = uid()
    enqueuePendingSseEvent(otherCr, makeEvent('task_update'))
    // The stale entry should have been evicted
    expect(getResolvedMessageId(cr)).toBeUndefined()
    vi.useRealTimers()
  })

  it('does not evict a fresh resolution entry (< 10 min)', () => {
    vi.useFakeTimers()
    const cr = uid()
    resolveClientRequestMessageId(cr, 'fresh-msg')
    // Advance to just under the TTL
    vi.advanceTimersByTime(10 * 60_000 - 1000)
    const otherCr = uid()
    enqueuePendingSseEvent(otherCr, makeEvent('task_update'))
    expect(getResolvedMessageId(cr)).toBe('fresh-msg')
    vi.useRealTimers()
  })
})
