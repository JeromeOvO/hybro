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
}

export const OrchestraTurn = React.memo(function OrchestraTurn({ turnLog }: OrchestraTurnProps) {
  const rawSlots = useTurnProjection(turnLog, contentSlotsReducer)
  const contentSlots = getVisibleSlots(rawSlots)
  const railItems = useTurnProjection(turnLog, railReducer)
  const composerState = useTurnProjection(turnLog, composerReducer)
  const userInput = turnLog.getUserInput()

  // Show rail when there are items OR when the turn is still processing
  const showRail = railItems.length > 0 || composerState.isProcessing

  return (
    <div data-testid="orchestra-turn">
      {userInput && <UserInputBlock data={userInput} />}
      {showRail && <OrchestrationRail items={railItems} isProcessing={composerState.isProcessing} />}
      {contentSlots.map(slot => (
        <ContentSlotRenderer key={slot.slotId} slot={slot} />
      ))}
    </div>
  )
})
