import { streamingBuffer } from './streaming-buffer'

/**
 * Client-side typewriter effect for progressive content reveal.
 *
 * When content arrives all at once (no real agent_token streaming), this feeds
 * text into the StreamingBuffer progressively so the existing streaming UI
 * (cursor, throttled markdown rendering) works transparently.
 *
 * Duration is intentionally short (~300ms) so the effect feels snappy rather
 * than slow. Each tick delivers a large chunk so the markdown renderer sees
 * meaningful increments rather than individual characters, which minimises
 * intermediate parse-state jank.
 */

const TICK_MS = 16 // ~60fps
const TARGET_TICKS = 20 // ~320ms at 16ms/tick
const MIN_CHARS_PER_TICK = 5

export interface TypewriterHandle {
  /** Stop the typewriter, jump to full content, and call onComplete. */
  finish: () => void
  /** Stop the typewriter WITHOUT calling onComplete. Clears the buffer entry. */
  abort: () => void
}

type OnComplete = () => void

/**
 * Start a typewriter effect for a message. Feeds `text` into the streaming
 * buffer chunk-by-chunk. Calls `onComplete` when done (or when `finish()` is
 * called manually). The caller is responsible for creating the ephemeral
 * entity before calling this and for upserting the final content in onComplete.
 */
export function startTypewriter(
  messageId: string,
  text: string,
  onComplete: OnComplete,
): TypewriterHandle {
  if (!text) {
    onComplete()
    return { finish: () => {}, abort: () => {} }
  }

  let offset = 0
  let done = false
  const charsPerTick = Math.max(MIN_CHARS_PER_TICK, Math.ceil(text.length / TARGET_TICKS))

  const timer = setInterval(() => {
    if (done) return

    const end = Math.min(offset + charsPerTick, text.length)
    streamingBuffer.appendTypewriter(messageId, text.slice(offset, end))
    offset = end

    if (offset >= text.length) {
      done = true
      clearInterval(timer)
      onComplete()
    }
  }, TICK_MS)

  // Seed the buffer so isStreaming(messageId) returns true immediately
  streamingBuffer.appendTypewriter(messageId, '')

  function finish() {
    if (done) return
    done = true
    clearInterval(timer)
    if (offset < text.length) {
      streamingBuffer.appendTypewriter(messageId, text.slice(offset))
    }
    onComplete()
  }

  function abort() {
    if (done) return
    done = true
    clearInterval(timer)
    streamingBuffer.finalize(messageId)
  }

  return { finish, abort }
}

/**
 * Manages active typewriter handles so they can be cleaned up on room switch
 * or when a new event supersedes the typewriter.
 */
export class TypewriterManager {
  private active = new Map<string, TypewriterHandle>()

  /** Start a typewriter. Finishes any existing one for the same messageId. */
  start(messageId: string, text: string, onComplete: OnComplete): void {
    this.finish(messageId)
    const handle = startTypewriter(messageId, text, () => {
      this.active.delete(messageId)
      onComplete()
    })
    this.active.set(messageId, handle)
  }

  /** Finish a specific typewriter immediately (jump to end + onComplete). */
  finish(messageId: string): void {
    this.active.get(messageId)?.finish()
    this.active.delete(messageId)
  }

  /** Abort a typewriter without calling onComplete. Used when real streaming supersedes. */
  abort(messageId: string): void {
    this.active.get(messageId)?.abort()
    this.active.delete(messageId)
  }

  /** Finish all active typewriters immediately. */
  finishAll(): void {
    for (const handle of this.active.values()) {
      handle.finish()
    }
    this.active.clear()
  }

  /** Check if a typewriter is active for a message. */
  isActive(messageId: string): boolean {
    return this.active.has(messageId)
  }
}
