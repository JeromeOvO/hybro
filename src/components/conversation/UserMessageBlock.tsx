'use client'

import { useState, useRef, useEffect } from 'react'
import type { MessageEntity } from '@/stores/message-store/types'
import { UserAttachmentCard } from './UserAttachmentCard'

interface UserMessageBlockProps {
  entity: MessageEntity
  onSentinelRef?: (el: HTMLDivElement | null) => void
}

export function UserMessageBlock({ entity, onSentinelRef }: UserMessageBlockProps) {
  const [expanded, setExpanded] = useState(false)
  const textRef = useRef<HTMLDivElement>(null)
  const [isOverflowing, setIsOverflowing] = useState(false)

  useEffect(() => {
    const el = textRef.current
    if (!el) return
    setIsOverflowing(el.scrollHeight > el.clientHeight + 1)
  }, [entity.content])

  const ts = new Date(entity.timestamp)
  const timeStr = ts.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

  return (
    <div
      ref={onSentinelRef}
      className="cursor-pointer"
      style={{ padding: '0 var(--conversation-padding-outer)' }}
      data-message-id={entity.id}
      onClick={() => isOverflowing && setExpanded(prev => !prev)}
    >
      <div
        className="rounded-lg border px-3 py-2.5"
        style={{
          backgroundColor: 'var(--conversation-surface)',
          borderColor: 'var(--conversation-border)',
        }}
      >
        <div className="flex items-start gap-2">
          <div className="w-6 h-6 rounded-full bg-zinc-700 flex items-center justify-center text-[10px] font-medium text-zinc-300 shrink-0">
            {(entity.senderName ?? 'U').charAt(0).toUpperCase()}
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 mb-0.5">
              <span className="text-sm font-medium" style={{ color: 'var(--conversation-text-primary)' }}>
                {entity.senderName}
              </span>
              <span className="text-xs" style={{ color: 'var(--conversation-text-dim)' }}>
                {timeStr}
              </span>
            </div>
            <div
              ref={textRef}
              className="text-sm leading-[1.5] break-words"
              style={{
                color: 'var(--conversation-text-secondary)',
                maxHeight: expanded ? 'none' : '4.5em',
                overflow: 'hidden',
                WebkitMaskImage: !expanded && isOverflowing
                  ? 'linear-gradient(to bottom, black 60%, transparent 100%)'
                  : undefined,
                maskImage: !expanded && isOverflowing
                  ? 'linear-gradient(to bottom, black 60%, transparent 100%)'
                  : undefined,
              }}
            >
              {entity.content}
            </div>
            {entity.attachments && entity.attachments.length > 0 && (
              <div className="flex flex-wrap gap-2 mt-2">
                {entity.attachments.map(att => (
                  <UserAttachmentCard key={att.fileId} attachment={att} />
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
