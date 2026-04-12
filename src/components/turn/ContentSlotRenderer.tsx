'use client'

import React from 'react'
import { AgentContentBlock } from './AgentContentBlock'
import { SummaryContentBlock } from './SummaryContentBlock'
import { HitlRecordBlock } from './HitlRecordBlock'
import type { ContentSlotView } from '@/stores/turn-event-store/types'

interface ContentSlotRendererProps {
  slot: ContentSlotView
}

export const ContentSlotRenderer = React.memo(function ContentSlotRenderer({ slot }: ContentSlotRendererProps) {
  switch (slot.slotType) {
    case 'agent':
      return <AgentContentBlock slot={slot} />
    case 'summary':
      return <SummaryContentBlock slot={slot} />
    case 'hitl_record':
      return <HitlRecordBlock slot={slot} />
  }
})
