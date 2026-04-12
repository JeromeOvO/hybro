'use client'

import React, { useState, useCallback, useRef } from 'react'
import { cn } from '@/lib/utils'

interface CursorMessageRowProps {
  avatarSlot: React.ReactNode
  children: React.ReactNode
  className?: string
  messageId?: string
  /** Render inline action bar when tapped (mobile). Receives dismiss callback. */
  mobileActions?: (dismiss: () => void) => React.ReactNode
}

/**
 * Shared layout primitive: fixed-width avatar column + flexible content column.
 * Provides `group` class for hover-triggered child visibility on desktop.
 * On touch devices, tracks tap state for inline action bar rendering.
 */
export const CursorMessageRow = React.memo(function CursorMessageRow({
  avatarSlot,
  children,
  className,
  messageId,
  mobileActions,
}: CursorMessageRowProps) {
  const [tapped, setTapped] = useState(false)
  const tapTimeoutRef = useRef<ReturnType<typeof setTimeout>>(null)

  const handleClick = useCallback(() => {
    // Only toggle tap state on touch-only devices (no hover capability).
    // On desktop, hover covers the UX so tapping is a no-op.
    if (!window.matchMedia('(hover: none)').matches) return
    setTapped(prev => !prev)
  }, [])

  // Allow parent to dismiss this row's mobile toolbar (e.g. when another row is tapped)
  const dismiss = useCallback(() => {
    setTapped(false)
    if (tapTimeoutRef.current) clearTimeout(tapTimeoutRef.current)
  }, [])

  return (
    <div
      className={cn(
        'group relative flex items-start gap-3 px-4 sm:px-5 py-1.5',
        'rounded-md transition-colors duration-150',
        'hover:bg-muted/15 dark:hover:bg-muted/8',
        className,
      )}
      data-message-id={messageId}
      onClick={handleClick}
    >
      {/* Avatar column — fixed 32px */}
      <div className="w-8 shrink-0 pt-0.5">{avatarSlot}</div>

      {/* Content column — fills remaining width */}
      <div className="min-w-0 flex-1">
        {children}

        {/* Mobile inline action bar — shown on tap when hover is unavailable */}
        {tapped && mobileActions && (
          <div className="flex items-center gap-2 py-1.5 border-t border-border/20 mt-2 animate-fade-in">
            {mobileActions(dismiss)}
          </div>
        )}
      </div>
    </div>
  )
})
