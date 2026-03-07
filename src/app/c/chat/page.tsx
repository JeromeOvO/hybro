"use client"

import { Suspense, useState, useEffect, useMemo, useCallback } from "react"
import { useSearchParams } from "next/navigation"
import { useUser, useClerk, useAuth } from "@clerk/nextjs"
import { RoomChatInput } from "@/components/room-chat-input"
import { GroupManagementModal } from "@/components/group-management-modal"
import { banner } from "@/components/ui/banner"
import {
    Loader2,
    AlertCircle,
    RefreshCw,
    FlaskConical,
    Code,
    PenLine,
    BarChart3,
    Sparkles,
    Users,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
    Tooltip,
    TooltipContent,
    TooltipProvider,
    TooltipTrigger,
} from "@/components/ui/tooltip"
import { useChatRoomCreation } from "@/hooks/useChatRoomCreation"
import { useGroupManagement } from "@/hooks/useGroupManagement"
import type { QuoteData } from "@/components/message-bubble"
import type { PendingAttachment } from "@/lib/types/attachments"
import { cn, isWaitlistEnabled } from "@/lib/utils"
import { getAgent } from "@/lib/api"
import type { Agent } from "@/lib/types/agent"

const quickStartTemplates = [
    { icon: FlaskConical, label: "Research", prompt: "Help me research " },
    { icon: Code, label: "Code", prompt: "Help me write code for " },
    { icon: PenLine, label: "Write", prompt: "Help me write " },
    { icon: BarChart3, label: "Analyze", prompt: "Help me analyze " },
]

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
    const { openWaitlist } = useClerk()
    const searchParams = useSearchParams()
    const [quickStartValue, setQuickStartValue] = useState("")
    const [hasError, setHasError] = useState(false)
    const [loadingAgent, setLoadingAgent] = useState(false)

    // Local mode toggles for the + menu
    const [localSupervisorMode, setLocalSupervisorMode] = useState(false)
    const [localDebateMode, setLocalDebateMode] = useState(false)

    const [preConfiguredRoom, setPreConfiguredRoom] = useState<{
        roomName: string
        selectedAgents: Agent[]
        debateMode: boolean
    } | null>(null)

    // Pre-configure room when agentId is in URL params
    const agentIdParam = searchParams.get("agentId")

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

    const {
        creating,
        createAndNavigate,
    } = useChatRoomCreation({
        userId: user?.id,
        userName: user?.firstName || user?.username || 'User',
        getToken
    })

    // Group management (extracted hook)
    const gm = useGroupManagement({
        userId: user?.id,
        getToken,
        isLoaded,
        roomAgentCount: preConfiguredRoom?.selectedAgents.length || 0,
    })

    // Agent list for mentions
    const agentListForMentions = useMemo(() => {
        return gm.availableAgents.map(agent => ({
            id: agent.agent_id,
            name: agent.agent_card.name
        }))
    }, [gm.availableAgents])

    const handleSubmit = async (value: string, _targetGroup?: string, _quote?: QuoteData | null, attachments?: PendingAttachment[]) => {
        if (!value.trim() && (!attachments || attachments.length === 0)) {
            banner.error("Please enter a message")
            return
        }
        if (!user?.id) {
            if (isWaitlistEnabled()) {
                openWaitlist()
            } else {
                window.location.href = "/sign-in"
            }
            return
        }

        try {
            setHasError(false)

            const options = {
                ...(preConfiguredRoom ? {
                    roomName: preConfiguredRoom.roomName || undefined,
                    selectedAgents: preConfiguredRoom.selectedAgents,
                } : {}),
                debateMode: localDebateMode,
                useSupervisor: localSupervisorMode,
                targetGroup: gm.selectedGroup,
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

    const handleQuickStart = (prompt: string) => {
        setQuickStartValue(prompt)
    }

    const handleClearQuickStart = () => {
        setQuickStartValue("")
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
            {/* Fixed Header */}
            <header className="shrink-0 flex items-center justify-between py-4 bg-background/95 backdrop-blur supports-backdrop-filter:bg-background/60 z-10 px-4 sm:px-6 max-w-4xl mx-auto w-full">
                    <div className="flex items-center gap-3">
                        <div className="space-y-1">
                            {preConfiguredRoom?.roomName && (
                                <h1 className="text-xl font-semibold">{preConfiguredRoom.roomName}</h1>
                            )}

                            {preConfiguredRoom && preConfiguredRoom.selectedAgents.length > 0 && (
                                <div className="flex items-center gap-2 flex-wrap">
                                    <TooltipProvider>
                                        <Tooltip>
                                            <TooltipTrigger asChild>
                                                <div className="flex items-center gap-1 text-xs text-muted-foreground">
                                                    <Users className="h-3 w-3" />
                                                    <span>Team: {preConfiguredRoom.selectedAgents.length} agent{preConfiguredRoom.selectedAgents.length !== 1 ? 's' : ''}</span>
                                                </div>
                                            </TooltipTrigger>
                                            <TooltipContent>
                                                <div className="space-y-1">
                                                    <p className="font-medium">Room team:</p>
                                                    {preConfiguredRoom.selectedAgents.map((agent, i) => (
                                                        <p key={i} className="text-xs">{agent.agent_card.name}</p>
                                                    ))}
                                                </div>
                                            </TooltipContent>
                                        </Tooltip>
                                    </TooltipProvider>
                                </div>
                            )}
                        </div>
                    </div>
            </header>
            <div className="flex-1 flex items-center justify-center p-4">
                <div className="w-full max-w-3xl">
                    {/* Header */}
                    <div className="text-center mb-8">
                        <h1 className="text-4xl font-bold mb-2">
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
                        <div className="flex items-center justify-center mb-6">
                            <div className="flex items-center gap-3 px-4 py-2 bg-primary/10 rounded-lg border border-primary/20">
                                <Loader2 className="h-4 w-4 animate-spin text-primary" />
                                <span className="text-sm">
                                    {loadingAgent ? "Loading agent..." : "Finding the best agents for you..."}
                                </span>
                            </div>
                        </div>
                    )}

                    {/* Chat Input with Group Selector */}
                    <div className="mb-6">
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
                            externalValue={quickStartValue}
                            onExternalValueConsumed={handleClearQuickStart}
                            supervisorMode={localSupervisorMode}
                            onSupervisorChange={setLocalSupervisorMode}
                            debateMode={localDebateMode}
                            onDebateModeChange={setLocalDebateMode}
                        />
                    </div>

                    {/* Quick start templates */}
                    <div className="space-y-3">
                        <div className="flex flex-wrap justify-center gap-2">
                            {quickStartTemplates.map((template) => (
                                <Button
                                    key={template.label}
                                    variant="outline"
                                    size="sm"
                                    onClick={() => handleQuickStart(template.prompt)}
                                    className="gap-2"
                                    disabled={creating}
                                >
                                    <template.icon className="h-4 w-4" />
                                    {template.label}
                                </Button>
                            ))}
                        </div>
                    </div>

                    {/* Tip */}
                    <div className="mt-8 text-center">
                        <p className="text-xs text-muted-foreground flex items-center justify-center gap-1.5">
                            <Sparkles className="h-3 w-3" />
                            {preConfiguredRoom?.selectedAgents.length === 1
                                ? `Type your message to start chatting with ${preConfiguredRoom.selectedAgents[0].agent_card.name}`
                                : "Just start typing \u2014 we\u2019ll find the best agents for your task"}
                        </p>
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
