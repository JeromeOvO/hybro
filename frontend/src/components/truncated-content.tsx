'use client'

import { useState, useRef, useEffect } from 'react'
import { cn } from '@/lib/utils'
import { MarkdownContent, LinkifiedContent } from './markdown-content'

interface TruncatedContentProps {
  content: string
  maxLines?: number
  className?: string
  /** Optional className forwarded to the inner MarkdownContent wrapper div */
  markdownClassName?: string
}

export function TruncatedContent({
  content,
  maxLines = 6,
  className,
  markdownClassName,
  isMarkdown = true,
}: TruncatedContentProps & { isMarkdown?: boolean }) {
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
        className="min-h-0 overflow-hidden break-words"
        style={!isExpanded ? { maxHeight: `${maxLines}lh` } : undefined}
      >
        {isMarkdown ? (
          <MarkdownContent content={content} className={markdownClassName} />
        ) : (
          <div className={markdownClassName}>
            <LinkifiedContent content={content} />
          </div>
        )}
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
          className="text-xs text-muted-foreground hover:text-foreground transition-colors mt-1 focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-2 rounded-sm"
          data-testid="truncated-toggle"
          aria-label={isExpanded ? 'Show less content' : 'Show more content'}
        >
          {isExpanded ? 'Show less' : 'Show more'}
        </button>
      )}
    </div>
  )
}
