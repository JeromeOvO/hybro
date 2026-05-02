'use client'

import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { AgentGroup } from '@/lib/types/agent-group'
import type { QuoteData } from '@/lib/types/quote'
import type { PendingAttachment } from '@/lib/types/attachments'
import type { ChatMode } from '@/lib/types/chat-mode'
import { ConversationMessageList } from '@/components/conversation/ConversationMessageList'
import { ComposerShell } from '@/components/composer/ComposerShell'
import { AgentResponseDetailPane } from '@/components/conversation/AgentResponseDetailPane'
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from '@/components/ui/resizable'
import { useMessageStore } from '@/stores/message-store'
import { useRoomUiStore, useSelectedAgentMessageId } from '@/stores/room-ui-store'
import { selectAgentResponseDetail } from '@/lib/selectors'
import type { MessageEntity } from '@/stores/message-store/types'

const EMPTY_ENTITIES: Record<string, MessageEntity> = {}
const EMPTY_ORDERED_IDS: string[] = []
const DETAIL_PANE_QUERY = '(min-width: 1280px)'
const DETAIL_PANE_LAYOUT = {
  'conversation-primary-panel': 66,
  'conversation-detail-panel': 34,
}

function canShowDetailPane(): boolean {
  if (typeof window === 'undefined' || !window.matchMedia) return true
  return window.matchMedia(DETAIL_PANE_QUERY).matches
}

function useCanShowDetailPane(): boolean {
  const [canShow, setCanShow] = useState(canShowDetailPane)

  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return
    const media = window.matchMedia(DETAIL_PANE_QUERY)
    const handleChange = () => setCanShow(media.matches)
    handleChange()
    media.addEventListener('change', handleChange)
    return () => media.removeEventListener('change', handleChange)
  }, [])

  return canShow
}

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
  const canShowAgentDetail = useCanShowDetailPane()
  const selectedMessageId = useSelectedAgentMessageId(adapter.roomId)
  const entities = useMessageStore(s => selectedMessageId ? s.entities : EMPTY_ENTITIES)
  const orderedIds = useMessageStore(s => selectedMessageId ? s.orderedIds : EMPTY_ORDERED_IDS)

  const detail = useMemo(() => {
    if (!canShowAgentDetail || !selectedMessageId) return null
    return selectAgentResponseDetail(adapter.roomId, selectedMessageId, entities, orderedIds)
  }, [adapter.roomId, canShowAgentDetail, selectedMessageId, entities, orderedIds])

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

  const primaryContent = (
    <div className="conversation-primary">
      <main className="flex-1 overflow-hidden">
        <ConversationMessageList
          roomId={adapter.roomId}
          selectedAgentMessageId={canShowAgentDetail ? selectedMessageId : undefined}
          enableAgentDetail={canShowAgentDetail}
        />
      </main>
      <div className="conversation-input-dock conversation-gutter">
        <div className="conversation-frame">
          <ComposerShell adapter={adapter} />
        </div>
      </div>
    </div>
  )

  return (
    <ResizablePanelGroup
      id={detail ? 'conversation-resizable-workspace' : undefined}
      orientation="horizontal"
      defaultLayout={detail ? DETAIL_PANE_LAYOUT : undefined}
      className="conversation-workspace conversation-workspace-resizable"
      data-detail-open={detail ? 'true' : undefined}
    >
      <ResizablePanel
        id="conversation-primary-panel"
        defaultSize={detail ? '66%' : '100%'}
        minSize={detail ? '54%' : '100%'}
        maxSize={detail ? '76%' : '100%'}
        className="conversation-resizable-panel"
        data-testid="conversation-primary-panel"
      >
        {primaryContent}
      </ResizablePanel>
      {detail && (
        <>
          <ResizableHandle
            id="conversation-detail-resize-handle"
            className="conversation-detail-resize-handle"
          />
          <ResizablePanel
            id="conversation-detail-panel"
            defaultSize="34%"
            minSize="24%"
            maxSize="46%"
            className="conversation-detail-resizable-panel"
            data-testid="conversation-detail-panel"
          >
            <AgentResponseDetailPane detail={detail} onClose={handleCloseDetail} />
          </ResizablePanel>
        </>
      )}
    </ResizablePanelGroup>
  )
}
