'use client'

import React from 'react'
import type { AgentGroup } from '@/lib/types/agent-group'
import type { QuoteData } from '@/lib/types/quote'
import type { PendingAttachment } from '@/lib/types/attachments'
import type { ChatMode } from '@/lib/types/chat-mode'
import { ConversationMessageList } from '@/components/conversation/ConversationMessageList'
import { ComposerShell } from '@/components/composer/ComposerShell'

export interface GroupManagementAdapter {
  groups: AgentGroup[]
  loadingGroups: boolean
  selectedGroup: string
  isOverride: boolean
  handleGroupChange: (groupId: string) => void
  handleClearOverride: () => void
  handleCreateGroup: () => void
  handleEditGroup: (group: AgentGroup) => void
  handleDeleteGroup: (group: AgentGroup) => void
  onEditRoomAgents: () => void
}

export interface QuoteState {
  quote: QuoteData | null
  setQuote: (data: QuoteData) => void
  clearQuote: () => void
}

export interface TimelineAdapter {
  roomId: string
  getToken?: () => Promise<string | null>
  onSendMessage: (message: string, targetGroup?: string, quoteData?: QuoteData | null, attachments?: PendingAttachment[]) => void
  onCancelProcessing: () => void
  onRespondToHitl: (hitlId: string, answer: string) => Promise<void>
  onChatModeChange: (mode: ChatMode) => void
  isSending: boolean
  isProcessing: boolean
  isCancelling: boolean
  agents: { id: string; name: string; iconUrl?: string }[]
  roomAgentIds: string[]
  groupManagement: GroupManagementAdapter
  quoteState: QuoteState
  chatMode: ChatMode
  externalValue?: string
  onExternalValueConsumed?: () => void
}

interface RoomPageShellProps {
  adapter: TimelineAdapter
}

export function RoomPageShell({ adapter }: RoomPageShellProps) {
  return (
    <>
      <main className="flex-1 overflow-hidden">
        <ConversationMessageList roomId={adapter.roomId} />
      </main>
      <div className="bg-background" style={{ borderTop: '1px solid var(--conversation-border)' }}>
        <div style={{ maxWidth: 'var(--conversation-max-width)', margin: '0 auto' }}>
          <ComposerShell adapter={adapter} />
        </div>
      </div>
    </>
  )
}
