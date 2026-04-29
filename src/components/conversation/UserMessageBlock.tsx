'use client'

import { useState, useRef, useEffect, useMemo, type ReactNode } from 'react'
import type { MessageEntity } from '@/stores/message-store/types'
import { UserAttachmentCard } from './UserAttachmentCard'

const MENTION_RE = /<@([^|]+)\|([^>]+)>/g

function renderContent(content: string): ReactNode[] {
  const parts: ReactNode[] = []
  let lastIndex = 0
  let match: RegExpExecArray | null

  MENTION_RE.lastIndex = 0
  while ((match = MENTION_RE.exec(content)) !== null) {
    if (match.index > lastIndex) {
      parts.push(content.slice(lastIndex, match.index))
    }
    const [, id, name] = match
    parts.push(
      <span key={`${id}-${match.index}`} className="room-mention" data-id={id} data-name={name}>
        @{name}
      </span>
    )
    lastIndex = MENTION_RE.lastIndex
  }

  if (lastIndex < content.length) {
    parts.push(content.slice(lastIndex))
  }
  return parts
}

interface UserMessageBlockProps {
  entity: MessageEntity
  isStuck?: boolean
}

export function UserMessageBlock({ entity, isStuck }: UserMessageBlockProps) {
  const [expanded, setExpanded] = useState(false)
  const textRef = useRef<HTMLDivElement>(null)
  const [isOverflowing, setIsOverflowing] = useState(false)

  useEffect(() => {
    const el = textRef.current
    if (!el) return
    setIsOverflowing(el.scrollHeight > el.clientHeight + 1)
  }, [entity.content])

  const rendered = useMemo(() => renderContent(entity.content ?? ''), [entity.content])

  return (
    <div
      className={`border border-border/50 bg-muted backdrop-blur-sm overflow-hidden transition-shadow duration-200 ${
        isOverflowing ? 'cursor-pointer' : ''
      } ${isStuck ? 'shadow-lg shadow-black/20' : ''}`}
      style={{ borderRadius: 12 }}
      data-message-id={entity.id}
      onClick={() => isOverflowing && setExpanded(prev => !prev)}
    >
      <div className="px-5 py-3">
        <div
          ref={textRef}
          className="text-[15px] leading-7 text-foreground break-words"
          style={{
            whiteSpace: 'pre-wrap',
            maxHeight: expanded ? 'none' : '84px',
            overflow: 'hidden',
            WebkitMaskImage: !expanded && isOverflowing
              ? 'linear-gradient(to bottom, black 50%, transparent 100%)'
              : undefined,
            maskImage: !expanded && isOverflowing
              ? 'linear-gradient(to bottom, black 50%, transparent 100%)'
              : undefined,
          }}
        >
          {rendered}
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
  )
}
