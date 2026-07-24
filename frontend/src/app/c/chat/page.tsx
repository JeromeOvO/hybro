"use client"

import { Suspense, useState, useEffect, useMemo, useCallback } from "react"
import { useSearchParams } from "next/navigation"
import { useUser, useAuth } from "@/lib/auth"
import { RoomChatInput } from "@/components/room-chat-input"
import { GroupManagementModal } from "@/components/group-management-modal"
import { banner } from "@/components/ui/banner"
import {
    Loader2,
    AlertCircle,
    RefreshCw,
    Sparkles,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { useChatRoomCreation } from "@/hooks/useChatRoomCreation"
import { useGroupManagement } from "@/hooks/useGroupManagement"
import type { QuoteData } from "@/lib/types/quote"
import type { PendingAttachment } from "@/lib/types/attachments"
import type { ChatMode } from "@/lib/types/chat-mode"
import { DEFAULT_CHAT_MODE, chatModeToFlags } from "@/lib/types/chat-mode"
import { cn } from "@/lib/utils"
import { getAgent } from "@/lib/api"
import type { Agent } from "@/lib/types/agent"
import { isMentionDispatchInput, type MessageDispatchInput } from "@/lib/types/agent-group"

export default function ChatPage() {
    return (
        <Suspense fallback={
            <div className="flex items-center justify-center h-full">
                <Loader2 className="h-8 w-8 animate-spin text-primary" />
            </div>
        }>
            <ChatPageContent />
        </Suspense>
    )
}

function ChatPageContent() {
    const { user, isLoaded } = useUser()
    const { getToken } = useAuth()
    const searchParams = useSearchParams()
    const [promptPrefill, setPromptPrefill] = useState("")
    const [hasError, setHasError] = useState(false)
    const [loadingAgent, setLoadingAgent] = useState(false)

    // Local chat mode selection
    const [localChatMode, setLocalChatMode] = useState<ChatMode>(DEFAULT_CHAT_MODE)

    const [preConfiguredRoom, setPreConfiguredRoom] = useState<{
        roomName: string
        selectedAgents: Agent[]
        debateMode: boolean
    } | null>(null)

    // Pre-configure room when agentId is in URL params
    const agentIdParam = searchParams.get("agentId")
    const promptParam = searchParams.get("prompt")

    useEffect(() => {
        if (promptParam) {
            setPromptPrefill(promptParam)
        }
    }, [promptParam])

    const loadAgentForChat = useCallback(async (agentId: string) => {
        try {
            setLoadingAgent(true)
            const response = await getAgent(agentId)
            if (response.success && response.agent) {
                const agent = response.agent
                const chatAgent: Agent = {
                    agent_id: agent.agent_id,
                    agent_card: agent.agent_card,
                    agent_status: agent.agent_status,
                }
                setPreConfiguredRoom({
                    roomName: `Chat with ${agent.agent_card.name}`,
                    selectedAgents: [chatAgent],
                    debateMode: false,
                })
            } else {
                console.error("Failed to load agent for chat:", response.error)
                banner.error("Could not load agent", {
                    description: "The agent may have been removed or is unavailable."
                })
            }
        } catch (error) {
            console.error("Failed to load agent for chat:", error)
            banner.error("Could not load agent")
        } finally {
            setLoadingAgent(false)
        }
    }, [])

    useEffect(() => {
        if (agentIdParam) {
            loadAgentForChat(agentIdParam)
        }
    }, [agentIdParam, loadAgentForChat])

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
        roomAgentCount: preConfiguredRoom?.selectedAgents.length || 0,
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

            const options = {
                ...(preConfiguredRoom ? {
                    roomName: preConfiguredRoom.roomName || undefined,
                    selectedAgents: preConfiguredRoom.selectedAgents,
                } : {}),
                debateMode: chatModeToFlags(localChatMode).debateMode,
                useSupervisor: chatModeToFlags(localChatMode).use_supervisor,
                dispatch,
                targetGroup: isMentionDispatchInput(dispatch) ? undefined : gm.selectedGroup,
                attachments,
            }

            const success = await createAndNavigate(value, options)

            if (!success) {
                throw new Error('Failed to create room')
            }
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

    const handlePromptPrefillConsumed = () => {
        setPromptPrefill("")
    }

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
                            {preConfiguredRoom?.selectedAgents.length === 1
                                ? `Ask ${preConfiguredRoom.selectedAgents[0].agent_card.name} anything`
                                : "What would you like to work on today?"}
                        </p>
                    </div>

                    {/* Creating state */}
                    {(creating || loadingAgent) && (
                        <div className="w-full max-w-3xl flex items-center justify-center mb-6">
                            <div className="flex items-center gap-3 px-4 py-2 bg-primary/10 rounded-lg border border-primary/20">
                                <Loader2 className="h-4 w-4 animate-spin text-primary" />
                                <span className="text-sm">
                                    {loadingAgent ? "Loading agent..." : "Finding the best agents for you..."}
                                </span>
                            </div>
                        </div>
                    )}

                    {/* Chat Input with Group Selector */}
                    <div className="w-full max-w-3xl mb-6">
                        <RoomChatInput
                            onSubmit={handleSubmit}
                            disableSend={creating}
                            agents={agentListForMentions}
                            showGroupSelector={true}
                            groups={gm.groups}
                            loadingGroups={gm.loadingGroups}
                            selectedGroup={gm.selectedGroup}
                            onGroupChange={gm.handleGroupChange}
                            roomAgentCount={preConfiguredRoom?.selectedAgents.length || 0}
                            onCreateGroup={gm.handleCreateGroup}
                            onEditGroup={gm.handleEditGroup}
                            onDeleteGroup={gm.handleDeleteGroup}
                            isOverride={gm.isOverride}
                            onClearOverride={gm.handleClearOverride}
                            externalValue={promptPrefill}
                            onExternalValueConsumed={handlePromptPrefillConsumed}
                            chatMode={localChatMode}
                            onChatModeChange={setLocalChatMode}
                        />
                    </div>

                    {/* Tip — only shown for pre-configured single-agent chat */}
                    {preConfiguredRoom?.selectedAgents.length === 1 && (
                        <div className="w-full max-w-3xl mt-6 md:mt-8 text-center px-2">
                            <p className="text-xs text-muted-foreground flex items-center justify-center gap-1.5">
                                <Sparkles className="h-3 w-3" />
                                {`Type your message to start chatting with ${preConfiguredRoom.selectedAgents[0].agent_card.name}`}
                            </p>
                        </div>
                    )}
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
