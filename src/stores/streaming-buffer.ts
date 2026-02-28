/**
 * Ephemeral token buffer for real-time agent token streaming.
 * 
 * This is NOT a Zustand store. It uses raw mutable state with useSyncExternalStore
 * for React integration, optimized for high write frequency (50-200 tokens/sec).
 * 
 * Design rationale:
 * - Streaming tokens are ephemeral (replaced by final agent_response content)
 * - High-frequency updates would be catastrophic for Zustand (reconciliation, re-renders)
 * - requestAnimationFrame batching caps React re-renders at 60fps
 * - useSyncExternalStore provides minimal subscription overhead
 * 
 * Performance notes:
 * - String concatenation in V8 is O(n) in total length (strings are immutable)
 * - For typical agent responses (< 10K chars) this is fast
 * - For very long responses (50K+ chars), consider switching to array-of-chunks
 *   representation and join only on read (deferred optimization)
 */

type Listener = () => void

class StreamingBuffer {
  private buffers = new Map<string, string>()
  private listeners = new Set<Listener>()
  private pendingFlush = false
  private version = 0
  private perMessageVersion = new Map<string, number>()

  /** Append a token to the buffer for a message. */
  append(messageId: string, token: string): void {
    const existing = this.buffers.get(messageId) ?? ''
    this.buffers.set(messageId, existing + token)
    this.perMessageVersion.set(messageId, (this.perMessageVersion.get(messageId) ?? 0) + 1)
    this.scheduleFlush()
  }

  /** Get accumulated text for a message (empty string if not streaming). */
  get(messageId: string): string {
    return this.buffers.get(messageId) ?? ''
  }

  /** Check if a message is currently streaming. */
  isStreaming(messageId: string): boolean {
    return this.buffers.has(messageId)
  }

  /** Remove buffer entry (called when agent_response arrives). Returns the accumulated content. */
  finalize(messageId: string): string {
    const content = this.buffers.get(messageId) ?? ''
    this.buffers.delete(messageId)
    this.perMessageVersion.set(messageId, (this.perMessageVersion.get(messageId) ?? 0) + 1)
    this.scheduleFlush()
    return content
  }

  /** Iterate over all active buffers (used by disconnect handler). */
  entries(): IterableIterator<[string, string]> {
    return this.buffers.entries()
  }

  /** Clear all buffers (room switch, disconnect). */
  clear(): void {
    this.buffers.clear()
    this.perMessageVersion.clear()
    this.scheduleFlush()
  }

  /** Subscribe for React useSyncExternalStore. */
  subscribe(listener: Listener): () => void {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  /** Global snapshot for useSyncExternalStore. Returns version number for change detection. */
  getSnapshot(): number {
    return this.version
  }

  /**
   * Per-message snapshot. Only changes when the specific message's buffer is
   * written or finalized, so non-streaming components bail out of re-rendering.
   */
  getMessageSnapshot(messageId: string): number {
    return this.perMessageVersion.get(messageId) ?? 0
  }

  private scheduleFlush(): void {
    if (this.pendingFlush) return
    this.pendingFlush = true
    requestAnimationFrame(() => {
      this.pendingFlush = false
      this.version++
      for (const listener of this.listeners) {
        listener()
      }
    })
  }
}

export const streamingBuffer = new StreamingBuffer()
