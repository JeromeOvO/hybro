'use client'

import React from 'react'
import { useMessageStore } from '@/stores/message-store'
import { selectComposerState } from '@/lib/selectors/select-composer-state'
import type { PendingHitl, ComposerState } from '@/lib/selectors/conversation-types'
import { HitlResponseBar, type HitlBatchAnswer, type HitlPromptView } from './HitlResponseBar'
import {
  RoomChatInput,
  type RoomChatInputAgent,
} from '@/components/room-chat-input'
import type { AgentGroup, MessageDispatchInput, TargetModeDispatchInput } from '@/lib/types/agent-group'
import type { QuoteData } from '@/lib/types/quote'
import type { PendingAttachment } from '@/lib/types/attachments'
import type { ChatMode } from '@/lib/types/chat-mode'
import { useCanonicalComposerAuthority } from '@/stores/turn-store'
import { Badge } from '@/components/ui/badge'

function toHitlPromptView(hitl: PendingHitl): HitlPromptView {
  return {
    hitlId: hitl.hitlId,
    source: hitl.source,
    agentName: hitl.agentName,
    prompt: hitl.question,
    promptType: hitl.promptType,
    choices: hitl.choices,
    interactionId: hitl.interactionId,
    lifecycleState: hitl.lifecycleState,
    errorMessage: hitl.errorMessage,
    clientRequestId: hitl.clientRequestId,
    answer: hitl.answer,
    groupIndex: hitl.groupIndex,
  }
}

export interface ComposerShellAdapter {
  roomId: string
  onSendMessage: (message: string, dispatch: MessageDispatchInput, quoteData?: QuoteData | null, attachments?: PendingAttachment[]) => void
  onCancelProcessing: () => void
  onRespondToHitlBatch: (interactionId: string, answers: HitlBatchAnswer[], clientRequestId?: string) => Promise<void>
  onCancelHitl: (requestId: string) => Promise<void>
  onRefreshHitl: () => Promise<void>
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
    roomMembershipLabel?: string
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

function samePendingHitl(left: PendingHitl, right: PendingHitl | undefined): boolean {
  if (!right) return false
  return (
    left.hitlId === right.hitlId
    && left.source === right.source
    && left.agentName === right.agentName
    && left.question === right.question
    && left.promptType === right.promptType
    && left.messageId === right.messageId
    && left.interactionId === right.interactionId
    && left.interactionStatus === right.interactionStatus
    && left.applicationStatus === right.applicationStatus
    && left.lifecycleState === right.lifecycleState
    && left.errorMessage === right.errorMessage
    && left.expiresAt === right.expiresAt
    && left.clientRequestId === right.clientRequestId
    && left.groupId === right.groupId
    && left.groupTotal === right.groupTotal
    && left.groupIndex === right.groupIndex
    && left.isAnswered === right.isAnswered
    && left.answer === right.answer
    && left.choices?.length === right.choices?.length
    && (left.choices ?? []).every((choice, index) => choice === right.choices?.[index])
  )
}

function useComposerState(roomId: string): ComposerState {
  const prev = React.useRef<ComposerState>({ mode: 'normal', isProcessing: false, pendingHitls: [] })
  return useMessageStore(s => {
    const next = selectComposerState(roomId, s.entities, s.orderedIds)
    if (
      prev.current.mode === next.mode &&
      prev.current.isProcessing === next.isProcessing &&
      prev.current.pendingHitls.length === next.pendingHitls.length &&
      prev.current.pendingHitls.every((hitl, index) => samePendingHitl(hitl, next.pendingHitls[index]))
    ) {
      return prev.current
    }
    prev.current = next
    return next
  })
}

export function ComposerShell({ adapter }: ComposerShellProps) {
  const composerState = useComposerState(adapter.roomId)
  const canonical = useCanonicalComposerAuthority(adapter.roomId)
  const isHitlMode = composerState.mode === 'hitl_responding'
    || (canonical.awaitingInput && composerState.pendingHitls.length > 0)
  // Mixed rooms retain independent authorities: a completed canonical Turn
  // must never mask an active legacy processing/HITL/send guard (or vice versa).
  const legacyProcessing = composerState.isProcessing || adapter.isProcessing
  const isProcessing = canonical.processing || legacyProcessing
  const normalComposerBlocked = canonical.normalComposerBlocked || legacyProcessing

  // Prefer an actionable open prompt over a transient applying recovery
  // state so follow-up HITL questions are not covered by "Applying…".
  const preferredHitl = composerState.pendingHitls.find(hitl => hitl.lifecycleState === 'open')
    ?? composerState.pendingHitls[0]
  const activeInteractionId = preferredHitl?.interactionId
  const activeHitls = activeInteractionId
    ? composerState.pendingHitls.filter(hitl => hitl.interactionId === activeInteractionId)
    : []

  if (isHitlMode && activeHitls.length > 0) {
    const queuedCount = composerState.pendingHitls.length - activeHitls.length
    return (
      <div className="conversation-hitl-response-frame" data-testid="hitl-response-frame">
        {queuedCount > 0 ? (
          <Badge variant="secondary" className="mb-2" data-testid="hitl-queue-note">
            {queuedCount === 1
              ? '1 more input request is queued after this one.'
              : `${queuedCount} more input requests are queued after this one.`}
          </Badge>
        ) : null}
        <HitlResponseBar
          hitls={activeHitls.map(toHitlPromptView)}
          onSubmit={adapter.onRespondToHitlBatch}
          onCancel={adapter.onCancelHitl}
          onRefresh={adapter.onRefreshHitl}
        />
      </div>
    )
  }

  return (
    <RoomChatInput
        onSubmit={adapter.onSendMessage}
        disableSend={adapter.isSending || normalComposerBlocked || isHitlMode}
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
        roomMembershipLabel={adapter.groupManagement.roomMembershipLabel}
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
  )
}
