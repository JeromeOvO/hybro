'use client'

import { useState, useRef, useEffect } from 'react'
import { cn } from '@/lib/utils'

interface TruncatedContentProps {
  content: string
  maxLines?: number
  className?: string
}

export function TruncatedContent({
  content,
  maxLines = 6,
  className,
}: TruncatedContentProps) {
  const [isExpanded, setIsExpanded] = useState(false)
  const [isTruncated, setIsTruncated] = useState(false)
  const contentRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = contentRef.current
    if (!el) return
    setIsTruncated(el.scrollHeight > el.clientHeight + 1)
  }, [content, maxLines])

  return (
    <div className={cn('relative', className)}>
      <div
        ref={contentRef}
        data-testid="truncated-content-body"
        className="whitespace-pre-wrap break-words"
        style={!isExpanded ? { WebkitLineClamp: maxLines, display: '-webkit-box', WebkitBoxOrient: 'vertical', overflow: 'hidden' } : undefined}
      >
        {content}
      </div>

      {isTruncated && !isExpanded && (
        <div
          className="absolute bottom-0 left-0 right-0 h-8 bg-gradient-to-t from-background to-transparent pointer-events-none"
          data-testid="truncated-fade"
          aria-hidden="true"
        />
      )}

      {isTruncated && (
        <button
          type="button"
          onClick={() => setIsExpanded((v) => !v)}
          className="text-xs text-muted-foreground hover:text-foreground transition-colors mt-1"
          data-testid="truncated-toggle"
        >
          {isExpanded ? 'Show less' : 'Show more'}
        </button>
      )}
    </div>
  )
}
