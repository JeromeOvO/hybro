'use client'

import React from 'react'
import { useMessageStore } from '@/stores/message-store'
import { selectComposerState } from '@/lib/selectors/select-composer-state'
import type { PendingHitl, ComposerState } from '@/lib/selectors/conversation-types'
import { HitlResponseBar, type HitlPromptView } from './HitlResponseBar'
import {
  RoomChatInput,
  type RoomChatInputAgent,
} from '@/components/room-chat-input'
import type { AgentGroup, MessageDispatchInput, TargetModeDispatchInput } from '@/lib/types/agent-group'
import type { QuoteData } from '@/lib/types/quote'
import type { PendingAttachment } from '@/lib/types/attachments'
import type { ChatMode } from '@/lib/types/chat-mode'

function toHitlPromptView(hitl: PendingHitl): HitlPromptView {
  return {
    hitlId: hitl.hitlId,
    turnId: hitl.messageId,
    ts: Date.now(),
    source: hitl.source,
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
  onSendMessage: (message: string, dispatch: MessageDispatchInput, quoteData?: QuoteData | null, attachments?: PendingAttachment[]) => void
  onCancelProcessing: () => void
  onRespondToHitl: (hitlId: string, answer: string) => Promise<void>
  onChatModeChange?: (mode: ChatMode) => void
  isSending: boolean
  isProcessing: boolean
  isCancelling: boolean
  agents: RoomChatInputAgent[]
  roomAgentIds: string[]
  groupManagement: {
    groups: AgentGroup[]
    loadingGroups: boolean
    selectedGroup: string
    selectedGroupName?: string
    resolvedTargetMode: TargetModeDispatchInput
    handleGroupChange: (groupId: string) => void
    handleCreateGroup: () => void
    handleEditGroup: (group: AgentGroup) => void
    handleDeleteGroup: (group: AgentGroup) => void
  }
  quoteState: {
    quote: QuoteData | null
    clearQuote: () => void
  }
  chatMode: ChatMode
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
  const isProcessing = composerState.isProcessing || adapter.isProcessing

  const hitlBar = composerState.pendingHitls.length > 0 ? (
    <div className="conversation-hitl-response-frame" data-testid="hitl-response-frame">
      <HitlResponseBar
        hitls={composerState.pendingHitls.map(toHitlPromptView)}
        onSubmit={adapter.onRespondToHitl}
      />
    </div>
  ) : undefined

  return (
    <>
      {hitlBar}
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
        selectedGroupName={adapter.groupManagement.selectedGroupName}
        selectedGroupDispatch={adapter.groupManagement.resolvedTargetMode}
        onGroupChange={adapter.groupManagement.handleGroupChange}
        onCreateGroup={adapter.groupManagement.handleCreateGroup}
        onEditGroup={adapter.groupManagement.handleEditGroup}
        onDeleteGroup={adapter.groupManagement.handleDeleteGroup}
        quote={adapter.quoteState.quote}
        onClearQuote={adapter.quoteState.clearQuote}
        chatMode={adapter.chatMode}
        onChatModeChange={adapter.onChatModeChange}
        externalValue={adapter.externalValue}
        onExternalValueConsumed={adapter.onExternalValueConsumed}
      />
    </>
  )
}
