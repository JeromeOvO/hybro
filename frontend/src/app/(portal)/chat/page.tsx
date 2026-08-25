'use client'

import { useState, useEffect, useMemo, useCallback } from "react"
import { useUser, useAuth } from "@/lib/auth"
import { RoomChatInput } from "@/components/room-chat-input"
import { GroupManagementModal } from "@/components/group-management-modal"
import { banner } from "@/components/ui/banner"
import {
    Loader2,
    AlertCircle,
    RefreshCw,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { useChatRoomCreation } from "@/hooks/useChatRoomCreation"
import { useGroupManagement } from "@/hooks/useGroupManagement"
import type { QuoteData } from "@/lib/types/quote"
import type { PendingAttachment } from "@/lib/types/attachments"
import type { Agent } from "@/lib/types/agent"
import type { ChatMode } from "@/lib/types/chat-mode"
import { DEFAULT_CHAT_MODE, chatModeToExecutionMode } from "@/lib/types/chat-mode"
import { cn } from "@/lib/utils"
import { useRoomUiStore } from "@/stores/room-ui-store"
import { UseCaseCard } from "@/components/use-case-card"
import { useCaseTemplates } from "@/lib/use-case-templates"
import type { UseCaseTemplate } from "@/lib/use-case-templates"
import { ensureUseCaseTeam } from '@/lib/use-case-team'
import { isMentionDispatchInput, type MessageDispatchInput } from "@/lib/types/agent-group"

export default function ChatPage() {
    return <ChatPageContent />
}

function ChatPageContent() {
    const { user, isLoaded } = useUser()
    const { getToken } = useAuth()
    const [promptPrefill, setPromptPrefill] = useState("")
    const [seedAgents, setSeedAgents] = useState<Agent[]>([])
    const [hasError, setHasError] = useState(false)
    const [settingUpTemplate, setSettingUpTemplate] = useState<string | null>(null)

    // Local chat mode selection
    const [localChatMode, setLocalChatMode] = useState<ChatMode>(DEFAULT_CHAT_MODE)

    useEffect(() => {
        // Peek first so an App Router/Strict Mode remount cannot consume the
        // handoff before the composer has actually applied it.
        const handoff = useRoomUiStore.getState().pendingChatHandoff
        if (!handoff) return
        setPromptPrefill(handoff.draft)
        if (handoff.seedAgents?.length) {
            setSeedAgents(handoff.seedAgents)
        }
        // Empty drafts never reach RoomChatInput's consumer; clear the store
        // once seed agents are copied into local state.
        if (!handoff.draft) {
            useRoomUiStore.getState().clearPendingChatHandoff()
        }
    }, [])

    const handleRequireAuth = useCallback(() => {
        window.location.href = `/sign-in?redirect_url=${encodeURIComponent(window.location.pathname + window.location.search)}`
    }, [])

    const {
        creating,
        createAndNavigate,
    } = useChatRoomCreation({
        userId: user?.id,
        userName: user?.firstName || user?.username || 'User',
        getToken,
        onRequireAuth: handleRequireAuth,
    })

    // Group management (extracted hook)
    const gm = useGroupManagement({
        userId: user?.id,
        getToken,
        isLoaded,
        onRequireAuth: handleRequireAuth,
    })

    // Agent list for mentions
    const agentListForMentions = useMemo(() => {
        return gm.availableAgents.map(agent => ({
            id: agent.agent_id,
            name: agent.agent_card.name,
            iconUrl: agent.agent_card.iconUrl,
        }))
    }, [gm.availableAgents])

    const handleSubmit = async (value: string, dispatch: MessageDispatchInput, _quote?: QuoteData | null, attachments?: PendingAttachment[]) => {
        if (!value.trim() && (!attachments || attachments.length === 0)) {
            banner.error("Please enter a message")
            return
        }
        if (!user?.id) {
            handleRequireAuth()
            return
        }

        try {
            setHasError(false)

            // Single-agent handoff seeds room membership and uses room_default,
            // unless the user explicitly @mentioned agents in this message.
            const seeded = seedAgents.length > 0
            const options = {
                useSupervisor: chatModeToExecutionMode(localChatMode) === 'supervisor',
                selectedAgents: seeded ? seedAgents : undefined,
                dispatch: seeded && !isMentionDispatchInput(dispatch)
                    ? { message_target_mode: 'room_default' as const }
                    : dispatch,
                targetGroup: isMentionDispatchInput(dispatch) || seeded
                    ? undefined
                    : gm.selectedGroup,
                attachments,
            }

            const success = await createAndNavigate(value, options)

            if (!success) {
                throw new Error('Failed to create room')
            }
            setSeedAgents([])
        } catch (error) {
            console.error('Error creating room:', error)
            setHasError(true)
            banner.error('Failed to start chat')

            // Revoke orphaned blob URLs — the input component already cleared
            // its attachment state so these URLs are unreachable by its cleanup.
            if (attachments) {
                for (const att of attachments) {
                    if (att.previewUrl) {
                        try { URL.revokeObjectURL(att.previewUrl) } catch { /* ignore */ }
                    }
                }
            }
        }
    }

    // Cards disabled only for logged-in users while catalog loads
    const catalogLoading = !!user && (
      gm.loadingAgents ||
      (!gm.loadingAgents && gm.availableAgents.length === 0 && !gm.agentsError)
    )

    const handleTemplateClick = async (template: UseCaseTemplate) => {
      if (!user?.id) {
        handleRequireAuth()
        return
      }

      setSettingUpTemplate(template.id)
      try {
        const team = await ensureUseCaseTeam({
          template,
          ownerId: user.id,
          catalog: gm.availableAgents,
          getToken,
        })
        gm.handleGroupCreated(team)
        setPromptPrefill(template.prefillMessage)
      } catch (error) {
        console.error('Failed to prepare use case:', error)
        banner.error(error instanceof Error ? error.message : 'Failed to prepare use case')
      } finally {
        setSettingUpTemplate(null)
      }
    }

    const handlePromptPrefillConsumed = useCallback(() => {
        setPromptPrefill("")
        useRoomUiStore.getState().clearPendingChatHandoff()
    }, [])

    if (!isLoaded) {
        return (
            <div className="flex items-center justify-center h-full">
                <Loader2 className="h-8 w-8 animate-spin text-primary" />
            </div>
        )
    }

    if (hasError) {
        return (
            <div className="flex items-center justify-center h-full p-4">
                <Card className="w-full max-w-md">
                    <CardHeader className="text-center">
                        <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-destructive/10">
                            <AlertCircle className="h-6 w-6 text-destructive" />
                        </div>
                        <CardTitle className="text-destructive">Something went wrong</CardTitle>
                        <CardDescription>
                            Failed to start chat. Please try again.
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <Button
                            onClick={() => setHasError(false)}
                            className="w-full"
                            disabled={creating}
                        >
                            {creating ? (
                                <>
                                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                    Retrying...
                                </>
                            ) : (
                                <>
                                    <RefreshCw className="mr-2 h-4 w-4" />
                                    Retry
                                </>
                            )}
                        </Button>
                        <Button
                            variant="outline"
                            onClick={() => setHasError(false)}
                            className="w-full"
                        >
                            Back to Chat
                        </Button>
                    </CardContent>
                </Card>
            </div>
        )
    }

    return (
        <div className="flex flex-col h-full bg-background">
            <div className="flex-1 flex items-center justify-center p-4">
                <div className="w-full flex flex-col items-center">
                    {/* Header */}
                    <div className="w-full max-w-3xl text-center mb-6 md:mb-8">
                        <h1 className="text-3xl md:text-4xl font-bold mb-2">
                            <span
                                className={cn(
                                    "font-bold font-spaceGrotesk text-[hsl(var(--color-hybro-hy))] mr-2",
                                )}
                            >
                                HY
                            </span>
                            <span
                                className={cn(
                                    "font-bold font-spaceGrotesk text-[hsl(var(--color-hybro-bro-strong))] dark:text-[hsl(var(--color-hybro-bro))]",
                                )}
                            >
                                BRO
                            </span>
                            <span className="ml-2 text-icon-exclaim inline-block text-3xl skew-x-150 scale-150 rotate-6">!</span>
                        </h1>
                        <p className="text-muted-foreground">
                            What would you like to work on today?
                        </p>
                    </div>

                    {/* Creating state */}
                    {(creating || settingUpTemplate) && (
                        <div className="w-full max-w-3xl flex items-center justify-center mb-6" role="status" aria-live="polite">
                            <div className="flex items-center gap-3 px-4 py-2 bg-primary/10 rounded-lg border border-primary/20">
                                <Loader2 className="h-4 w-4 animate-spin text-primary" />
                                <span className="text-sm">
                                    {settingUpTemplate ? 'Preparing your preset team...' : 'Finding the best agents for you...'}
                                </span>
                            </div>
                        </div>
                    )}

                    {/* Chat Input with Group Selector */}
                    <div className="w-full max-w-3xl mb-6">
                        <RoomChatInput
                            onSubmit={handleSubmit}
                            disableSend={creating || settingUpTemplate !== null}
                            agents={agentListForMentions}
                            showGroupSelector={true}
                            groups={gm.groups}
                            loadingGroups={gm.loadingGroups}
                            selectedGroup={gm.selectedGroup}
                            selectedGroupName={
                              seedAgents.length === 1
                                ? seedAgents[0].agent_card.name
                                : seedAgents.length > 1
                                  ? `Room Team (${seedAgents.length})`
                                  : gm.selectedGroupName
                            }
                            selectedGroupDispatch={
                              seedAgents.length > 0
                                ? { message_target_mode: 'room_default' }
                                : gm.resolvedTargetMode
                            }
                            onGroupChange={(groupId) => {
                              // User took over scope selection; stop forcing the
                              // single-agent seed label/dispatch.
                              setSeedAgents([])
                              gm.handleGroupChange(groupId)
                            }}
                            onCreateGroup={gm.handleCreateGroup}
                            onEditGroup={gm.handleEditGroup}
                            onDeleteGroup={gm.handleDeleteGroup}
                            externalValue={promptPrefill}
                            onExternalValueConsumed={handlePromptPrefillConsumed}
                            chatMode={localChatMode}
                            onChatModeChange={setLocalChatMode}
                        />
                    </div>

                    {/* Use Case Cards */}
                    <div className="w-full max-w-3xl mx-auto">
                        {/* Separator */}
                        <div className="flex items-center gap-3 px-4 mb-6">
                            <div className="flex-1 h-px bg-border/60" />
                            <span className="text-xs font-medium text-muted-foreground tracking-wide uppercase">
                                Featured Use Cases
                            </span>
                            <div className="flex-1 h-px bg-border/60" />
                        </div>

                        {gm.agentsError && gm.availableAgents.length === 0 ? (
                            <div className="flex items-center justify-center py-8">
                                <p className="text-sm text-muted-foreground">
                                    To Be Continued
                                </p>
                            </div>
                        ) : (
                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 px-2">
                                {useCaseTemplates.map((template) => (
                                    <UseCaseCard
                                        key={template.id}
                                        template={template}
                                        catalog={gm.availableAgents}
                                        onClick={() => handleTemplateClick(template)}
                                        disabled={creating || settingUpTemplate !== null || catalogLoading || gm.loadingGroups}
                                    />
                                ))}
                            </div>
                        )}
                    </div>

                </div>
            </div>

            {/* Group Management Modal */}
            <GroupManagementModal
                open={gm.groupManagementOpen}
                onOpenChange={(open) => {
                    gm.setGroupManagementOpen(open)
                    if (!open) {
                        gm.setGroupAction(null)
                    }
                }}
                groups={gm.groups}
                onGroupsChange={gm.handleGroupsChange}
                onGroupCreated={gm.handleGroupCreated}
                availableAgents={gm.availableAgents}
                loadingAgents={gm.loadingAgents}
                userId={user?.id || ''}
                getToken={getToken}
                initialAction={gm.groupAction || undefined}
            />
        </div>
    )
}
