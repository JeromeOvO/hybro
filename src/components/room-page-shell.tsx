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
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@/components/ui/sheet'
import { useMessageStore } from '@/stores/message-store'
import { useStreamingStore } from '@/stores/streaming-store'
import { useRoomUiStore, useSelectedAgentMessageId } from '@/stores/room-ui-store'
import { selectAgentResponseDetail } from '@/lib/selectors'
import type { MessageEntity } from '@/stores/message-store/types'

const EMPTY_ENTITIES: Record<string, MessageEntity> = {}
const EMPTY_ORDERED_IDS: string[] = []
const DETAIL_PANE_QUERY = '(min-width: 1024px)'
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
  // Always read entities so the mobile sheet has data too
  const entities = useMessageStore(s => selectedMessageId ? s.entities : EMPTY_ENTITIES)
  const orderedIds = useMessageStore(s => selectedMessageId ? s.orderedIds : EMPTY_ORDERED_IDS)
  // Subscribe to streaming buffers so the detail pane shows live content
  // during streaming (entity.content is empty until task_update checkpoint).
  const buffers = useStreamingStore(s => s.buffers)

  // Compute detail regardless of breakpoint — needed for both side pane and mobile sheet
  const detail = useMemo(() => {
    if (!selectedMessageId) return null
    return selectAgentResponseDetail(adapter.roomId, selectedMessageId, entities, orderedIds, buffers)
  }, [adapter.roomId, selectedMessageId, entities, orderedIds, buffers])

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

  // Track the input dock height so the scroll area always pads enough to
  // clear it — prevents agent cards near the bottom from being occluded by
  // the dock and becoming non-clickable on mobile.
  const dockRef = useRef<HTMLDivElement>(null)
  const primaryRef = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    const dock = dockRef.current
    const primary = primaryRef.current
    if (!dock || !primary) return

    const update = () => {
      primary.style.setProperty('--conversation-dock-height', `${dock.offsetHeight}px`)
    }
    update()

    const ro = new ResizeObserver(update)
    ro.observe(dock)
    return () => ro.disconnect()
  }, [])

  // On desktop the side pane is visible; on mobile the sheet takes over
  const desktopDetail = canShowAgentDetail ? detail : null

  const primaryContent = (
    <div ref={primaryRef} className="conversation-primary">
      <main className="flex-1 overflow-hidden">
        <ConversationMessageList
          roomId={adapter.roomId}
          selectedAgentMessageId={selectedMessageId}
          enableAgentDetail
        />
      </main>
      <div ref={dockRef} className="conversation-input-dock conversation-gutter">
        <div className="conversation-frame">
          <ComposerShell adapter={adapter} />
        </div>
      </div>
    </div>
  )

  return (
    <>
      <ResizablePanelGroup
        id={desktopDetail ? 'conversation-resizable-workspace' : undefined}
        orientation="horizontal"
        defaultLayout={desktopDetail ? DETAIL_PANE_LAYOUT : undefined}
        className="conversation-workspace conversation-workspace-resizable"
        data-detail-open={desktopDetail ? 'true' : undefined}
      >
        <ResizablePanel
          id="conversation-primary-panel"
          defaultSize={desktopDetail ? '66%' : '100%'}
          minSize={desktopDetail ? '54%' : '100%'}
          maxSize={desktopDetail ? '76%' : '100%'}
          className="conversation-resizable-panel"
          data-testid="conversation-primary-panel"
        >
          {primaryContent}
        </ResizablePanel>
        {desktopDetail && (
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
              <AgentResponseDetailPane detail={desktopDetail} onClose={handleCloseDetail} />
            </ResizablePanel>
          </>
        )}
      </ResizablePanelGroup>

      {/* Mobile sheet — shown when a card is tapped on narrow screens */}
      {!canShowAgentDetail && (
        <Sheet open={!!detail} onOpenChange={(open) => { if (!open) handleCloseDetail() }}>
          <SheetContent
            side="bottom"
            className="h-[85dvh] p-0 flex flex-col gap-0 rounded-t-xl overflow-hidden sm:min-w-[480px] sm:max-w-2xl sm:inset-x-auto sm:left-1/2 sm:-translate-x-1/2"
            data-mobile-sheet
          >
            <SheetTitle className="sr-only">Agent response detail</SheetTitle>
            <SheetDescription className="sr-only">View the full response from the agent</SheetDescription>
            {detail && (
              <AgentResponseDetailPane detail={detail} onClose={handleCloseDetail} />
            )}
          </SheetContent>
        </Sheet>
      )}
    </>
  )
}
