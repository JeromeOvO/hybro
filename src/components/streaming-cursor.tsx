'use client'

/**
 * Streaming cursor indicator for real-time token streaming.
 * Shows a blinking cursor at the end of streaming text.
 */
export function StreamingCursor() {
  return (
    <span 
      className="inline-block w-2 h-4 bg-foreground/60 animate-pulse ml-0.5 align-text-bottom"
      aria-hidden="true"
    />
  )
}
