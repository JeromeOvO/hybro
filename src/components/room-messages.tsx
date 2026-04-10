'use client'

import { ConversationTimeline } from './conversation-timeline'
import type { QuoteData } from './message-bubble'

interface RoomMessagesProps {
  onQuote?: (data: QuoteData) => void
}

export function RoomMessages({ onQuote }: RoomMessagesProps) {
  return <ConversationTimeline onQuote={onQuote} />
}
