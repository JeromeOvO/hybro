'use client'

import React from 'react'
import { Sparkles } from 'lucide-react'
import { MarkdownContent } from '@/components/markdown-content'
import type { ContentSlotView } from '@/stores/turn-event-store/types'

interface SummaryContentBlockProps {
  slot: ContentSlotView
}

export const SummaryContentBlock = React.memo(function SummaryContentBlock({ slot }: SummaryContentBlockProps) {
  const modeLabel = slot.mode === 'debate' ? 'Debate Summary' : 'Supervisor Summary'

  return (
    <div className="py-3" data-testid="summary-content-block">
      <div className="flex items-center gap-2 mb-2 px-1">
        <div className="flex items-center justify-center w-7 h-7 rounded-md shrink-0 bg-violet-500/10 border border-violet-500/20 text-violet-600 dark:text-violet-400">
          <Sparkles className="h-4 w-4" />
        </div>
        <span className="font-semibold text-base text-foreground">HYBRO AI</span>
        <span className="text-xs text-muted-foreground">{modeLabel}</span>
      </div>
      <div className="pl-10 pr-2">
        {slot.content && (
          <div className="prose prose-sm dark:prose-invert max-w-none">
            <MarkdownContent content={slot.content} />
          </div>
        )}
      </div>
    </div>
  )
})
