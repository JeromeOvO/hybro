'use client'

import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef } from 'react'
import type { AgentGroup, MessageDispatchInput, TargetModeDispatchInput } from '@/lib/types/agent-group'
import type { QuoteData } from '@/lib/types/quote'
import type { PendingAttachment } from '@/lib/types/attachments'
import type { ChatMode } from '@/lib/types/chat-mode'
import { ConversationMessageList } from '@/components/conversation/ConversationMessageList'
import { ComposerShell } from '@/components/composer/ComposerShell'
import { useTextSelectionQuote } from '@/hooks/useTextSelectionQuote'
import { AgentResponseDetailPane } from '@/components/conversation/AgentResponseDetailPane'
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from '@/components/ui/resizable'
import { useGroupRef } from 'react-resizable-panels'
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@/components/ui/sheet'
import { useSidebar } from '@/components/ui/sidebar'
import { useMessageStore } from '@/stores/message-store'
import { useStreamBuffer } from '@/hooks/useStreamBuffer'
import { useRoomUiStore, useSelectedAgentMessageId } from '@/stores/room-ui-store'
import { selectAgentResponseDetail } from '@/lib/selectors/select-agent-response-detail'
import type { MessageEntity } from '@/stores/message-store/types'

const EMPTY_ENTITIES: Record<string, MessageEntity> = {}
const EMPTY_ORDERED_IDS: string[] = []
const DETAIL_PANE_QUERY = '(min-width: 1024px)'
const DETAIL_PANE_LAYOUT = {
  'conversation-primary-panel': 50,
  'conversation-detail-panel': 50,
}

function canShowDetailPane(): boolean {
  if (typeof window === 'undefined' || !window.matchMedia) return true
  return window.matchMedia(DETAIL_PANE_QUERY).matches
}

function useCanShowDetailPane(): boolean {
  const [canShow, setCanShow] = React.useState(canShowDetailPane)

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
  selectedGroupName?: string
  resolvedTargetMode: TargetModeDispatchInput
  handleGroupChange: (groupId: string) => void
  handleCreateGroup: () => void
  handleEditGroup: (group: AgentGroup) => void
  handleDeleteGroup: (group: AgentGroup) => void
}

export interface QuoteState {
  quote: QuoteData | null
  setQuote: (data: QuoteData) => void
  clearQuote: () => void
}

export interface TimelineAdapter {
  roomId: string
  getToken?: () => Promise<string | null>
  onSendMessage: (message: string, dispatch: MessageDispatchInput, quoteData?: QuoteData | null, attachments?: PendingAttachment[]) => void
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
  const {
    open: sidebarOpen,
    openMobile: sidebarOpenMobile,
    setOpen: setSidebarOpen,
    setOpenMobile: setSidebarOpenMobile,
    isMobile: isMobileSidebar,
  } = useSidebar()
  const conversationGroupRef = useGroupRef()
  const prevSelectedMessageIdRef = useRef<string | undefined>(undefined)
  const sidebarSnapshotRef = useRef<{ desktopOpen: boolean; mobileOpen: boolean } | null>(null)
  const selectedMessageId = useSelectedAgentMessageId(adapter.roomId)
  const entities = useMessageStore(s => selectedMessageId ? s.entities : EMPTY_ENTITIES)
  const orderedIds = useMessageStore(s => selectedMessageId ? s.orderedIds : EMPTY_ORDERED_IDS)
  const streamBuffer = useStreamBuffer(selectedMessageId)

  const detail = useMemo(() => {
    if (!selectedMessageId) return null
    return selectAgentResponseDetail(
      adapter.roomId,
      selectedMessageId,
      entities,
      orderedIds,
      streamBuffer,
    )
  }, [adapter.roomId, selectedMessageId, entities, orderedIds, streamBuffer])

  const restoreSidebar = useCallback(() => {
    const snapshot = sidebarSnapshotRef.current
    if (!snapshot) return
    if (isMobileSidebar) {
      setSidebarOpenMobile(snapshot.mobileOpen)
    } else {
      setSidebarOpen(snapshot.desktopOpen)
    }
    sidebarSnapshotRef.current = null
  }, [isMobileSidebar, setSidebarOpen, setSidebarOpenMobile])

  const prevRoomIdRef = useRef(adapter.roomId)
  useEffect(() => {
    if (adapter.roomId !== prevRoomIdRef.current) {
      restoreSidebar()
      useRoomUiStore.getState().closeAgentDetail(prevRoomIdRef.current)
      prevRoomIdRef.current = adapter.roomId
      prevSelectedMessageIdRef.current = undefined
    }
  }, [adapter.roomId, restoreSidebar])

  useLayoutEffect(() => {
    if (selectedMessageId && !detail) {
      restoreSidebar()
      useRoomUiStore.getState().closeAgentDetail(adapter.roomId)
    }
  }, [selectedMessageId, detail, adapter.roomId, restoreSidebar])

  const handleCloseDetail = useCallback(() => {
    prevSelectedMessageIdRef.current = undefined
    restoreSidebar()
    useRoomUiStore.getState().closeAgentDetail(adapter.roomId)
  }, [adapter.roomId, restoreSidebar])

  useLayoutEffect(() => {
    if (!selectedMessageId) return

    if (sidebarSnapshotRef.current !== null) return

    sidebarSnapshotRef.current = {
      desktopOpen: sidebarOpen,
      mobileOpen: sidebarOpenMobile,
    }
    if (isMobileSidebar) {
      setSidebarOpenMobile(false)
    } else {
      setSidebarOpen(false)
    }
  }, [
    selectedMessageId,
    sidebarOpen,
    sidebarOpenMobile,
    isMobileSidebar,
    setSidebarOpen,
    setSidebarOpenMobile,
  ])

  const dockRef = useRef<HTMLDivElement>(null)
  const primaryRef = useRef<HTMLDivElement>(null)
  const workspaceHostRef = useRef<HTMLDivElement>(null)

  useTextSelectionQuote(workspaceHostRef, adapter.quoteState.setQuote)
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

  const desktopDetail = canShowAgentDetail ? detail : null

  useLayoutEffect(() => {
    if (!desktopDetail || !selectedMessageId) {
      if (!selectedMessageId) prevSelectedMessageIdRef.current = undefined
      return
    }
    if (prevSelectedMessageIdRef.current === selectedMessageId) return

    const group = conversationGroupRef.current
    if (group) {
      try {
        group.setLayout(DETAIL_PANE_LAYOUT)
      } catch {
        // Group may not be measured yet in non-layout environments.
      }
    }
    prevSelectedMessageIdRef.current = selectedMessageId
  }, [desktopDetail, selectedMessageId, conversationGroupRef])

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
      <div ref={workspaceHostRef} className="conversation-workspace-host relative h-full min-h-0">
        <ResizablePanelGroup
          id={desktopDetail ? 'conversation-resizable-workspace' : undefined}
          groupRef={conversationGroupRef}
          orientation="horizontal"
          defaultLayout={desktopDetail ? DETAIL_PANE_LAYOUT : undefined}
          className="conversation-workspace conversation-workspace-resizable"
          data-detail-open={desktopDetail ? 'true' : undefined}
        >
          <ResizablePanel
            id="conversation-primary-panel"
            defaultSize={desktopDetail ? '50%' : '100%'}
            minSize={desktopDetail ? '15%' : '100%'}
            maxSize={desktopDetail ? '80%' : '100%'}
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
                defaultSize="50%"
                minSize="20%"
                maxSize="85%"
                className="conversation-detail-resizable-panel"
                data-testid="conversation-detail-panel"
              >
                <AgentResponseDetailPane detail={desktopDetail} onClose={handleCloseDetail} />
              </ResizablePanel>
            </>
          )}
        </ResizablePanelGroup>
      </div>

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
