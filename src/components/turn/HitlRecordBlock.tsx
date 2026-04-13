'use client'

import React from 'react'
import { MessageCircleQuestion } from 'lucide-react'
import type { ContentSlotView } from '@/stores/turn-event-store/types'

interface HitlRecordBlockProps {
  slot: ContentSlotView
}

export const HitlRecordBlock = React.memo(function HitlRecordBlock({ slot }: HitlRecordBlockProps) {
  const sourceLabel = slot.hitlSource === 'agent'
    ? (slot.agentName ?? 'Agent')
    : 'HYBRO AI'

  return (
    <div className="py-3" data-testid="hitl-record-block">
      <div className="flex items-center gap-2 mb-2 px-1">
        <div className="flex items-center justify-center w-7 h-7 rounded-md shrink-0 bg-amber-500/10 border border-amber-500/20 text-amber-600 dark:text-amber-400">
          <MessageCircleQuestion className="h-4 w-4" />
        </div>
        <span className="font-medium text-sm text-muted-foreground">Input Record</span>
      </div>
      <div className="pl-10 pr-2 space-y-1.5">
        <p className="text-sm text-foreground">
          <span className="font-medium">{sourceLabel}</span> asked: {slot.hitlPrompt}
        </p>
        {slot.hitlAnswer && (
          <p className="text-sm text-foreground">
            <span className="font-medium">Your answer:</span> {slot.hitlAnswer}
          </p>
        )}
      </div>
    </div>
  )
})
