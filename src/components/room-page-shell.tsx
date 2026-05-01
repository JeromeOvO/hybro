'use client'

import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef } from 'react'
import type { AgentGroup } from '@/lib/types/agent-group'
import type { QuoteData } from '@/lib/types/quote'
import type { PendingAttachment } from '@/lib/types/attachments'
import type { ChatMode } from '@/lib/types/chat-mode'
import { ConversationMessageList } from '@/components/conversation/ConversationMessageList'
import { ComposerShell } from '@/components/composer/ComposerShell'
import { AgentResponseDetailPane } from '@/components/conversation/AgentResponseDetailPane'
import { useMessageStore } from '@/stores/message-store'
import { useRoomUiStore, useSelectedAgentMessageId } from '@/stores/room-ui-store'
import { selectAgentResponseDetail } from '@/lib/selectors'
import type { MessageEntity } from '@/stores/message-store/types'

const EMPTY_ENTITIES: Record<string, MessageEntity> = {}
const EMPTY_ORDERED_IDS: string[] = []

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
  const selectedMessageId = useSelectedAgentMessageId(adapter.roomId)
  const entities = useMessageStore(s => selectedMessageId ? s.entities : EMPTY_ENTITIES)
  const orderedIds = useMessageStore(s => selectedMessageId ? s.orderedIds : EMPTY_ORDERED_IDS)

  const detail = useMemo(() => {
    if (!selectedMessageId) return null
    return selectAgentResponseDetail(adapter.roomId, selectedMessageId, entities, orderedIds)
  }, [adapter.roomId, selectedMessageId, entities, orderedIds])

  const prevRoomIdRef = useRef(adapter.roomId)
  useEffect(() => {
    if (adapter.roomId !== prevRoomIdRef.current) {
      useRoomUiStore.getState().closeAgentDetail(prevRoomIdRef.current)
      prevRoomIdRef.current = adapter.roomId
    }
  }, [adapter.roomId])

  useLayoutEffect(() => {
    if (selectedMessageId && !detail) {
      useRoomUiStore.getState().closeAgentDetail(adapter.roomId)
    }
  }, [selectedMessageId, detail, adapter.roomId])

  const handleCloseDetail = useCallback(() => {
    useRoomUiStore.getState().closeAgentDetail(adapter.roomId)
  }, [adapter.roomId])

  return (
    <div
      className="conversation-workspace"
      data-detail-open={detail ? 'true' : undefined}
    >
      <div className="conversation-primary">
        <main className="flex-1 overflow-hidden">
          <ConversationMessageList
            roomId={adapter.roomId}
            selectedAgentMessageId={selectedMessageId}
          />
        </main>
        <div className="conversation-input-dock conversation-gutter">
          <div className="conversation-frame">
            <ComposerShell adapter={adapter} />
          </div>
        </div>
      </div>
      {detail && (
        <AgentResponseDetailPane detail={detail} onClose={handleCloseDetail} />
      )}
    </div>
  )
}
