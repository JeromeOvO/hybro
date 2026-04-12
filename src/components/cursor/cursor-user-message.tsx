'use client'

import React, { useState, useRef, useCallback } from 'react'
import { ChevronDown, ChevronUp } from 'lucide-react'
import { cn } from '@/lib/utils'
import { formatTimestamp } from '@/lib/time'
import { LinkifiedContent } from '@/components/markdown-content'
import { UserAttachmentCard } from '@/components/message-bubble'
import { CursorMessageRow } from './cursor-message-row'
import { CursorMobileActions } from './cursor-hover-actions'
import type { MessageEntity } from '@/stores/message-store'
import type { AttachmentData } from '@/lib/types/attachments'

// ── User avatar ────────────────────────────────────────────────

function UserAvatar({ name }: { name: string }) {
  const initials = name
    .split(/\s+/)
    .slice(0, 2)
    .map(w => w[0])
    .join('')
    .toUpperCase() || 'U'

  return (
    <div
      className="w-7 h-7 rounded-full bg-primary/15 text-primary flex items-center justify-center font-semibold text-[11px] shrink-0 select-none"
      aria-hidden="true"
    >
      {initials}
    </div>
  )
}

// ── Main component ─────────────────────────────────────────────

interface CursorUserMessageProps {
  entity: MessageEntity
}

export const CursorUserMessage = React.memo(function CursorUserMessage({
  entity,
}: CursorUserMessageProps) {
  const displayContent = entity.content || ''
  const isLong = displayContent.length > 500
  const [expanded, setExpanded] = useState(false)
  const toggleRef = useRef<HTMLButtonElement>(null)
  const estimatedLines = isLong ? Math.max(5, Math.ceil(displayContent.length / 80)) : 0

  const handleToggle = useCallback(() => {
    const next = !expanded
    const btn = toggleRef.current
    const container = btn?.closest('[data-message-scroll-container="true"]') as HTMLElement | null
    const prevBottom = btn?.getBoundingClientRect().bottom

    setExpanded(next)

    // Preserve scroll position when collapsing
    if (btn && container && !next && typeof prevBottom === 'number') {
      container.dataset.programmaticScroll = 'true'
      requestAnimationFrame(() => {
        const newBottom = btn.getBoundingClientRect().bottom
        const delta = newBottom - prevBottom
        if (delta !== 0) container.scrollTop += delta
        requestAnimationFrame(() => {
          container.dataset.programmaticScroll = 'false'
        })
      })
    }
  }, [expanded])

  return (
    <CursorMessageRow
      avatarSlot={<UserAvatar name={entity.senderName} />}
      messageId={entity.id}
      mobileActions={(dismiss) => (
        <CursorMobileActions
          content={displayContent}
          messageId={entity.id}
          senderName={entity.senderName}
          timestamp={formatTimestamp(entity.timestamp)}
          onDismiss={dismiss}
        />
      )}
    >
      {/* Header: name + timestamp */}
      <div className="flex items-baseline gap-2 mb-1">
        <span className="text-[13px] font-semibold text-foreground">You</span>
        <span className="cursor-timestamp text-xs text-muted-foreground/50 opacity-0 group-hover:opacity-100 transition-opacity">
          {formatTimestamp(entity.timestamp)}
        </span>
      </div>

      {/* Content with subtle background wash for "self" affordance */}
      {displayContent && (
        <div className="bg-muted/30 dark:bg-muted/15 rounded-lg px-3 py-2">
          <div className="relative">
            <div
              className={cn(
                'text-[15px] leading-relaxed text-foreground transition-opacity duration-200',
                !expanded && isLong && 'max-h-[5lh] overflow-hidden',
              )}
            >
              <LinkifiedContent content={displayContent} />
            </div>
            {!expanded && isLong && (
              <div className="absolute inset-x-0 bottom-0 h-8 bg-gradient-to-t from-muted/30 dark:from-muted/15 to-transparent pointer-events-none" />
            )}
          </div>

          {isLong && (
            <button
              ref={toggleRef}
              type="button"
              onClick={handleToggle}
              className="flex items-center gap-1 text-xs mt-2 text-muted-foreground hover:text-foreground transition-colors"
            >
              {expanded ? (
                <>
                  <ChevronUp className="h-3.5 w-3.5" />
                  Show less
                </>
              ) : (
                <>
                  <ChevronDown className="h-3.5 w-3.5" />
                  Show more ({estimatedLines} lines)
                </>
              )}
            </button>
          )}
        </div>
      )}

      {/* Attachments */}
      {entity.attachments && entity.attachments.length > 0 && (
        <div className="flex flex-wrap gap-2 mt-2">
          {entity.attachments.map((att: AttachmentData) => (
            <UserAttachmentCard key={att.fileId} attachment={att} />
          ))}
        </div>
      )}
    </CursorMessageRow>
  )
})
