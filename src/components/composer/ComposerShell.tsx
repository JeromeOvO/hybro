'use client'

import React from 'react'
import { useMessageStore } from '@/stores/message-store'
import { selectComposerState } from '@/lib/selectors'
import type { PendingHitl, ComposerState } from '@/lib/selectors/conversation-types'
import { HitlResponseBar, type HitlPromptView } from './HitlResponseBar'
import { RoomChatInput } from '@/components/room-chat-input'

function toHitlPromptView(hitl: PendingHitl): HitlPromptView {
  return {
    hitlId: hitl.hitlId,
    turnId: hitl.messageId,
    ts: Date.now(),
    source: 'agent',
    agentName: hitl.agentName,
    prompt: hitl.question,
    promptType: hitl.promptType,
    choices: hitl.choices,
    groupId: hitl.groupId,
    groupTotal: hitl.groupTotal,
    groupIndex: hitl.groupIndex,
  }
}

export interface ComposerShellAdapter {
  roomId: string
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
  externalValue?: string
  onExternalValueConsumed?: () => void
}

interface ComposerShellProps {
  adapter: ComposerShellAdapter
}

function useComposerState(roomId: string): ComposerState {
  const prev = React.useRef<ComposerState>({ mode: 'normal', isProcessing: false, pendingHitls: [] })
  return useMessageStore(s => {
    const next = selectComposerState(roomId, s.entities, s.orderedIds)
    if (
      prev.current.mode === next.mode &&
      prev.current.isProcessing === next.isProcessing &&
      prev.current.pendingHitls.length === next.pendingHitls.length &&
      prev.current.pendingHitls.every((h, i) => h.hitlId === next.pendingHitls[i]?.hitlId && h.isAnswered === next.pendingHitls[i]?.isAnswered)
    ) {
      return prev.current
    }
    prev.current = next
    return next
  })
}

export function ComposerShell({ adapter }: ComposerShellProps) {
  const composerState = useComposerState(adapter.roomId)
  const isHitlMode = composerState.mode === 'hitl_responding'
  const isProcessing = composerState.isProcessing && adapter.isProcessing

  const hitlBar = composerState.pendingHitls.length > 0 ? (
    <HitlResponseBar
      hitls={composerState.pendingHitls.map(toHitlPromptView)}
      onSubmit={adapter.onRespondToHitl}
    />
  ) : undefined

  return (
    <RoomChatInput
      onSubmit={adapter.onSendMessage}
      disableSend={adapter.isSending || isProcessing || isHitlMode}
      sending={adapter.isSending}
      processing={isProcessing}
      cancelling={adapter.isCancelling && isProcessing}
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
      externalValue={adapter.externalValue}
      onExternalValueConsumed={adapter.onExternalValueConsumed}
    />
  )
}
