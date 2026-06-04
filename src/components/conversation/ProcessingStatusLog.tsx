'use client'

import { useEffect, useRef, useState } from 'react'
import { ChevronRight } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { ProcessingStatusLogEntry } from '@/stores/message-store/types'
import { INITIAL_PROCESSING_STATUS_MESSAGE } from '@/hooks/room/processing-status-log'

interface ProcessingStatusLogProps {
  entries: ProcessingStatusLogEntry[]
  isRunning?: boolean
}

export function ProcessingStatusLog({ entries, isRunning = false }: ProcessingStatusLogProps) {
  const [isExpanded, setIsExpanded] = useState(false)
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const userPinnedRef = useRef(false)
  const displayEntries = entries.length > 0
    ? entries
    : [{
        id: 'processing-log-default',
        message: INITIAL_PROCESSING_STATUS_MESSAGE,
        timestamp: new Date().toISOString(),
      }]
  const entryCountLabel = `${displayEntries.length} ${displayEntries.length === 1 ? 'update' : 'updates'}`
  const activeEntryId = isRunning ? displayEntries[displayEntries.length - 1]?.id : undefined

  useEffect(() => {
    if (userPinnedRef.current) return
    const scroll = scrollRef.current
    if (!scroll) return
    scroll.scrollTop = scroll.scrollHeight
  }, [displayEntries.length, isExpanded])

  return (
    <section
      className={cn(
        'conversation-processing-log',
        isRunning && 'conversation-processing-log-running',
      )}
    >
      <button
        type="button"
        className="conversation-processing-log-trigger"
        style={{ justifyContent: 'flex-start' }}
        aria-expanded={isExpanded}
        aria-label={`Work Logs, ${entryCountLabel}`}
        onClick={() => setIsExpanded((expanded) => !expanded)}
      >
        <ChevronRight
          className={cn(
            'conversation-processing-log-chevron',
            isExpanded && 'conversation-processing-log-chevron-open',
          )}
          aria-hidden="true"
        />
        <span className="conversation-processing-log-title">Work Logs</span>
      </button>
      <div
        ref={scrollRef}
        className="conversation-processing-log-scroll"
        role="log"
        aria-live="polite"
        data-state={isExpanded ? 'expanded' : 'compact'}
        style={{
          height: isExpanded
            ? 'var(--conversation-processing-log-expanded-height)'
            : 'var(--conversation-processing-log-compact-height)',
        }}
        onScroll={(event) => {
          const target = event.currentTarget
          userPinnedRef.current =
            target.scrollHeight - target.scrollTop - target.clientHeight > 24
        }}
      >
        {displayEntries.map((entry) => {
          const isActive = entry.id === activeEntryId
          return (
            <div
              key={entry.id}
              className={cn(
                'conversation-processing-log-row',
                isActive && 'conversation-processing-log-row-active',
              )}
            >
              <span
                className={cn(
                  'conversation-processing-log-message',
                  isActive && 'conversation-processing-log-message-active',
                )}
              >
                {entry.message}
              </span>
            </div>
          )
        })}
      </div>
    </section>
  )
}
