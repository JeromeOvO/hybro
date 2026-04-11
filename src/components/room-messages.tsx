'use client'

import { ConversationTimeline } from './conversation-timeline'
import type { QuoteData } from './message-bubble'

interface RoomMessagesProps {
  roomAgentList?: { agentId: string; agentName: string }[]
  onQuote?: (data: QuoteData) => void
}

export function RoomMessages({ roomAgentList, onQuote }: RoomMessagesProps) {
  return <ConversationTimeline roomAgentList={roomAgentList} onQuote={onQuote} />
}
