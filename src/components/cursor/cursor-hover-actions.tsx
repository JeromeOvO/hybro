'use client'

import React, { useState, useCallback, useRef, useEffect } from 'react'
import { Copy, Check, Quote } from 'lucide-react'
import { cn } from '@/lib/utils'

interface CursorHoverActionsProps {
  content: string
  messageId: string
  senderName: string
  onQuote?: (data: { messageId: string; content: string; senderName: string }) => void
  /** When true, the toolbar hides to avoid conflicting with text-selection quote button. */
  selectionActive?: boolean
}

/**
 * Floating toolbar shown on message hover (desktop).
 * Renders Copy + Quote buttons. Hides when text selection quote is active.
 *
 * On touch devices this component is invisible — mobile actions are handled
 * via the inline action bar in CursorMessageRow.
 */
export function CursorHoverActions({
  content,
  messageId,
  senderName,
  onQuote,
  selectionActive = false,
}: CursorHoverActionsProps) {
  const [copied, setCopied] = useState(false)
  const timeoutRef = useRef<ReturnType<typeof setTimeout>>(null)

  useEffect(() => {
    return () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current)
    }
  }, [])

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(content)
      setCopied(true)
      if (timeoutRef.current) clearTimeout(timeoutRef.current)
      timeoutRef.current = setTimeout(() => setCopied(false), 1500)
    } catch {
      // Clipboard API may fail in insecure contexts — silent fallback
    }
  }, [content])

  const handleQuote = useCallback(() => {
    onQuote?.({ messageId, content, senderName })
  }, [onQuote, messageId, content, senderName])

  return (
    <div
      className={cn(
        'absolute -top-3 right-2 z-10',
        'flex items-center gap-0.5 rounded-md',
        'border border-border/40 bg-background/95 backdrop-blur-sm shadow-sm',
        'px-1 py-0.5',
        // Visibility: hidden by default, shown on parent group hover
        'opacity-0 pointer-events-none',
        'group-hover:opacity-100 group-hover:pointer-events-auto',
        'transition-opacity duration-150',
        // Hide on touch devices (mobile uses inline actions)
        'touch-action:none',
        // Hide when text selection quote button is active
        selectionActive && '!opacity-0 !pointer-events-none',
      )}
      // Prevent clicks from bubbling to the message row (avoids mobile tap toggle)
      onClick={(e) => e.stopPropagation()}
    >
      <button
        type="button"
        onClick={handleCopy}
        className="p-1 rounded hover:bg-muted transition-colors"
        aria-label={copied ? 'Copied' : 'Copy message'}
      >
        {copied ? (
          <Check className="h-3.5 w-3.5 text-emerald-500" />
        ) : (
          <Copy className="h-3.5 w-3.5 text-muted-foreground" />
        )}
      </button>
      {onQuote && (
        <button
          type="button"
          onClick={handleQuote}
          className="p-1 rounded hover:bg-muted transition-colors"
          aria-label="Quote message"
        >
          <Quote className="h-3.5 w-3.5 text-muted-foreground" />
        </button>
      )}
    </div>
  )
}

/**
 * Inline mobile actions row — rendered inside CursorMessageRow on tap.
 * Same Copy + Quote functionality but inline rather than floating.
 */
export function CursorMobileActions({
  content,
  messageId,
  senderName,
  timestamp,
  onQuote,
  onDismiss,
}: {
  content: string
  messageId: string
  senderName: string
  timestamp?: string
  onQuote?: (data: { messageId: string; content: string; senderName: string }) => void
  onDismiss: () => void
}) {
  const [copied, setCopied] = useState(false)
  const timeoutRef = useRef<ReturnType<typeof setTimeout>>(null)

  useEffect(() => {
    return () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current)
    }
  }, [])

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(content)
      setCopied(true)
      if (timeoutRef.current) clearTimeout(timeoutRef.current)
      timeoutRef.current = setTimeout(() => {
        setCopied(false)
        onDismiss()
      }, 1200)
    } catch {
      // silent
    }
  }, [content, onDismiss])

  const handleQuote = useCallback(() => {
    onQuote?.({ messageId, content, senderName })
    onDismiss()
  }, [onQuote, messageId, content, senderName, onDismiss])

  return (
    <>
      <button
        type="button"
        onClick={handleCopy}
        className="flex items-center gap-1 px-2 py-1 rounded text-xs text-muted-foreground hover:text-foreground transition-colors"
      >
        {copied ? <Check className="h-3 w-3 text-emerald-500" /> : <Copy className="h-3 w-3" />}
        {copied ? 'Copied' : 'Copy'}
      </button>
      {onQuote && (
        <button
          type="button"
          onClick={handleQuote}
          className="flex items-center gap-1 px-2 py-1 rounded text-xs text-muted-foreground hover:text-foreground transition-colors"
        >
          <Quote className="h-3 w-3" />
          Quote
        </button>
      )}
      {timestamp && (
        <span className="ml-auto text-xs text-muted-foreground/50">{timestamp}</span>
      )}
    </>
  )
}
