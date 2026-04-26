'use client'

import React from 'react'
import type { TurnEventLog } from '@/stores/turn-event-store/event-log'
import { useTurnProjection } from '@/hooks/turn/useTurnProjection'
import { contentSlotsReducer, getVisibleSlots } from '@/stores/turn-event-store/projections/content-slots'
import { railReducer } from '@/stores/turn-event-store/projections/rail'
import { composerReducer } from '@/stores/turn-event-store/projections/composer'
import { UserInputBlock } from './UserInputBlock'
import { ContentSlotRenderer } from './ContentSlotRenderer'
import { OrchestrationRail } from './OrchestrationRail'

interface OrchestraTurnProps {
  turnLog: TurnEventLog
  footerSlot?: React.ReactNode
}

export const OrchestraTurn = React.memo(function OrchestraTurn({ turnLog, footerSlot }: OrchestraTurnProps) {
  const rawSlots = useTurnProjection(turnLog, contentSlotsReducer)
  const contentSlots = getVisibleSlots(rawSlots)
  const railItems = useTurnProjection(turnLog, railReducer)
  const composerState = useTurnProjection(turnLog, composerReducer)
  const userInput = turnLog.getUserInput()

  // Show rail when there are items OR when the turn is still processing
  const showRail = railItems.length > 0 || composerState.isProcessing

  return (
    <div data-testid="orchestra-turn">
      {userInput && (
        <div
          data-message-id={turnLog.turnId}
          className="sticky top-0 z-10 bg-background pb-1 shadow-[0_4px_6px_-1px_rgba(0,0,0,0.05)]"
        >
          <UserInputBlock data={userInput} />
        </div>
      )}
      {showRail && (
        <div className="pt-1">
          <OrchestrationRail items={railItems} isProcessing={composerState.isProcessing} />
        </div>
      )}
      {contentSlots.map(slot => (
        <ContentSlotRenderer key={slot.slotId} slot={slot} />
      ))}
      {footerSlot}
    </div>
  )
})
