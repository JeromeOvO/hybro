'use client'

import React from 'react'
import { useTurnEventStore } from '@/stores/turn-event-store'
import { HitlResponseBar } from './HitlResponseBar'
import { RoomChatInput } from '@/components/room-chat-input'

// Adapter interface for props passed from RoomPageShell
export interface ComposerShellAdapter {
  onSendMessage: (...args: any[]) => void
  onCancelProcessing: () => void
  onRespondToHitl: (hitlId: string, answer: string) => Promise<void>
  onChatModeChange?: (mode: any) => void
  isSending: boolean
  isProcessing: boolean
  isCancelling: boolean
  agents: any[]
  roomAgentIds: string[]
  groupManagement: {
    groups: any[]
    loadingGroups: boolean
    selectedGroup: string
    isOverride: boolean
    handleGroupChange: (groupId: string) => void
    handleClearOverride: () => void
    handleCreateGroup: () => void
    handleEditGroup: (group: any) => void
    handleDeleteGroup: (group: any) => void
    onEditRoomAgents: () => void
  }
  quoteState: {
    quote: any
    setQuote: (data: any) => void
    clearQuote: () => void
  }
  chatMode: any
}

interface ComposerShellProps {
  adapter: ComposerShellAdapter
}

export function ComposerShell({ adapter }: ComposerShellProps) {
  const composerState = useTurnEventStore(s => s.composerState)
  const isHitlMode = composerState.mode === 'hitl_responding'

  const hitlBar = composerState.pendingHitls.length > 0 ? (
    <HitlResponseBar
      hitls={composerState.pendingHitls}
      onSubmit={adapter.onRespondToHitl}
    />
  ) : undefined

  return (
    <RoomChatInput
      onSubmit={adapter.onSendMessage}
      disableSend={adapter.isSending || adapter.isProcessing || isHitlMode}
      sending={adapter.isSending}
      processing={adapter.isProcessing}
      cancelling={adapter.isCancelling}
      onCancel={adapter.onCancelProcessing}
      agents={adapter.agents}
      roomAgentIds={adapter.roomAgentIds}
      showGroupSelector={!isHitlMode}
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
      topSlot={hitlBar}
    />
  )
}
