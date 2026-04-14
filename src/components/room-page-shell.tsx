'use client'

import React from 'react'
import type { AgentGroup } from '@/lib/types/agent-group'
import type { QuoteData } from '@/components/message-bubble'
import type { PendingAttachment } from '@/lib/types/attachments'
import type { ChatMode } from '@/lib/types/chat-mode'

// ── TimelineAdapter interface ─────────────────────────────────

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
  // Identity
  roomId: string
  getToken?: () => Promise<string | null>

  // Actions
  onSendMessage: (message: string, targetGroup?: string, quoteData?: QuoteData | null, attachments?: PendingAttachment[]) => void
  onCancelProcessing: () => void
  onRespondToHitl: (hitlId: string, answer: string) => Promise<void>
  onChatModeChange: (mode: ChatMode) => void

  // State: processing & sending
  isSending: boolean
  isProcessing: boolean
  isCancelling: boolean

  // State: agents & room
  agents: { id: string; name: string; iconUrl?: string }[]
  roomAgentIds: string[]

  // State + actions: group management
  groupManagement: GroupManagementAdapter

  // State: quote
  quoteState: QuoteState

  // State: chat mode
  chatMode: ChatMode
}

// ── View switcher ─────────────────────────────────────────────

import { TurnList } from '@/components/turn/TurnList'
import { ComposerShell } from '@/components/composer/ComposerShell'
import { useTurnHydration } from '@/hooks/turn/useTurnHydration'
import { useMessageStoreSync } from '@/hooks/turn/useMessageStoreSync'
import { RoomMessages } from '@/components/room-messages'
import { RoomChatInput } from '@/components/room-chat-input'
import { HitlPanel } from '@/components/hitl-inline-reply-form'
import { useActiveHitlRequests } from '@/hooks/useRoomMessages'

interface TurnBasedViewProps {
  adapter: TimelineAdapter
}

function TurnBasedView({ adapter }: TurnBasedViewProps) {
  useTurnHydration(adapter.roomId, adapter.getToken)
  useMessageStoreSync() // Bridge legacy SSE → turn events when Redis/journal is down

  return (
    <>
      <main className="flex-1 overflow-hidden">
        <TurnList />
      </main>
      <div className="bg-background p-4">
        <div className="max-w-4xl mx-auto">
          <ComposerShell adapter={adapter} />
        </div>
      </div>
    </>
  )
}

interface LegacyViewProps {
  adapter: TimelineAdapter
}

function LegacyView({ adapter }: LegacyViewProps) {
  const activeHitlRequests = useActiveHitlRequests()

  return (
    <>
      <main className="flex-1 overflow-hidden">
        <RoomMessages onQuote={adapter.quoteState.setQuote} />
      </main>
      <div className="bg-background p-4">
        <div className="max-w-4xl mx-auto">
          <RoomChatInput
            onSubmit={adapter.onSendMessage}
            disableSend={adapter.isSending || adapter.isProcessing}
            sending={adapter.isSending}
            processing={adapter.isProcessing}
            cancelling={adapter.isCancelling}
            onCancel={adapter.onCancelProcessing}
            agents={adapter.agents}
            roomAgentIds={adapter.roomAgentIds}
            showGroupSelector={true}
            groups={adapter.groupManagement.groups}
            loadingGroups={adapter.groupManagement.loadingGroups}
            selectedGroup={adapter.groupManagement.selectedGroup}
            onGroupChange={adapter.groupManagement.handleGroupChange}
            roomAgentCount={adapter.roomAgentIds.length}
            onCreateGroup={adapter.groupManagement.handleCreateGroup}
            onEditGroup={adapter.groupManagement.handleEditGroup}
            onDeleteGroup={adapter.groupManagement.handleDeleteGroup}
            onEditRoomAgents={adapter.groupManagement.onEditRoomAgents}
            isOverride={adapter.groupManagement.isOverride}
            onClearOverride={adapter.groupManagement.handleClearOverride}
            quote={adapter.quoteState.quote}
            onClearQuote={adapter.quoteState.clearQuote}
            chatMode={adapter.chatMode}
            onChatModeChange={adapter.onChatModeChange}
            topSlot={activeHitlRequests.length > 0
              ? <HitlPanel requests={activeHitlRequests} onSubmit={adapter.onRespondToHitl} />
              : undefined
            }
          />
        </div>
      </div>
    </>
  )
}

// ── Exported shell ────────────────────────────────────────────

interface RoomPageShellProps {
  adapter: TimelineAdapter
  turnBasedTimeline: boolean
}

export function RoomPageShell({ adapter, turnBasedTimeline }: RoomPageShellProps) {
  if (turnBasedTimeline) {
    return <TurnBasedView adapter={adapter} />
  }
  return <LegacyView adapter={adapter} />
}
