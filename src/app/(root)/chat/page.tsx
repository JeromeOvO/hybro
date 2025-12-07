"use client"

import { useState, useEffect, useMemo, useCallback } from "react"
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
    Settings,
    Users,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
import {
    Tooltip,
    TooltipContent,
    TooltipProvider,
    TooltipTrigger,
} from "@/components/ui/tooltip"
import { RoomSettingForm } from "@/components/room-setting-form"
import { useChatRoomCreation } from "@/hooks/useChatRoomCreation"
import { cn, isWaitlistEnabled } from "@/lib/utils"
import { listAgentGroups } from "@/lib/api/agent-group"
import { getAllAgents } from "@/lib/api/agent"
import type { AgentGroup } from "@/lib/types/agent-group"
import type { Agent } from "@/lib/types/agent"
import { BUILTIN_GROUP_ALL_AGENTS, BUILTIN_GROUP_ROOM_TEAM } from "@/lib/types/agent-group"

const quickStartTemplates = [
    { icon: FlaskConical, label: "Research", prompt: "Help me research " },
    { icon: Code, label: "Code", prompt: "Help me write code for " },
    { icon: PenLine, label: "Write", prompt: "Help me write " },
    { icon: BarChart3, label: "Analyze", prompt: "Help me analyze " },
]

export default function ChatPage() {
    const { user, isLoaded } = useUser()
    const { getToken } = useAuth()
    const [quickStartValue, setQuickStartValue] = useState("")
    const [hasError, setHasError] = useState(false)
    const [selectedGroup, setSelectedGroup] = useState<string>(BUILTIN_GROUP_ALL_AGENTS)
    const [isOverride, setIsOverride] = useState(false)  // Track if override is active
    const [groups, setGroups] = useState<AgentGroup[]>([])
    const [loadingGroups, setLoadingGroups] = useState(false)
    const { openWaitlist } = useClerk()
    
    // Group management modal state
    const [groupManagementOpen, setGroupManagementOpen] = useState(false)
    const [availableAgents, setAvailableAgents] = useState<Agent[]>([])
    const [loadingAgents, setLoadingAgents] = useState(false)
    
    // Room settings dialog state
    const [roomSettingsOpen, setRoomSettingsOpen] = useState(false)
    const [preConfiguredRoom, setPreConfiguredRoom] = useState<{
        roomName: string
        selectedAgents: Agent[]
        debateMode: boolean
    } | null>(null)
    
    const { 
        creating, 
        createAndNavigate,
    } = useChatRoomCreation({
        userId: user?.id,
        userName: user?.firstName || user?.username || 'User',
        getToken
    })

    // Load user's groups
    useEffect(() => {
        const loadGroups = async () => {
            if (!user?.id) return
            
            setLoadingGroups(true)
            try {
                const response = await listAgentGroups(user.id, getToken)
                if (response.success && response.groups) {
                    setGroups(response.groups)
                }
            } catch (error) {
                console.error('Failed to load groups:', error)
            } finally {
                setLoadingGroups(false)
            }
        }

        if (isLoaded && user?.id) {
            loadGroups()
        }
    }, [isLoaded, user?.id, getToken])

    // Load agents for group management modal and mention suggestions
    const loadAvailableAgents = useCallback(async () => {
        if (availableAgents.length > 0) return
        setLoadingAgents(true)
        try {
            const response = await getAllAgents()
            if (response.success && response.agents) {
                setAvailableAgents(response.agents)
            }
        } catch (error) {
            console.error('Failed to load agents:', error)
        } finally {
            setLoadingAgents(false)
        }
    }, [availableAgents.length])
    
    // Load agents on mount for mention suggestions
    useEffect(() => {
        if (isLoaded && user?.id && availableAgents.length === 0) {
            loadAvailableAgents()
        }
    }, [isLoaded, user?.id, loadAvailableAgents, availableAgents.length])
    
    // Agent list for mentions - always show all available agents regardless of selected scope
    const agentListForMentions = useMemo(() => {
        return availableAgents.map(agent => ({
            id: agent.agent_id,
            name: agent.agent_card.name
        }))
    }, [availableAgents])

    // Refresh groups after changes in modal
    const handleGroupsChange = async () => {
        if (!user?.id) return
        try {
            const response = await listAgentGroups(user.id, getToken)
            if (response.success && response.groups) {
                setGroups(response.groups)
            }
        } catch (error) {
            console.error('Failed to refresh groups:', error)
        }
    }

    // Open group management modal
    const handleManageGroups = () => {
        loadAvailableAgents()
        setGroupManagementOpen(true)
    }

    // Handle group change (override)
    const handleGroupChange = (groupId: string) => {
        setSelectedGroup(groupId)
        setIsOverride(true)
    }

    // Handle clear override - revert to default
    const handleClearOverride = () => {
        setIsOverride(false)
        // Default: Room Team if pre-configured agents exist, otherwise All Agents
        const defaultGroup = (preConfiguredRoom?.selectedAgents.length || 0) > 0 
            ? BUILTIN_GROUP_ROOM_TEAM 
            : BUILTIN_GROUP_ALL_AGENTS
        setSelectedGroup(defaultGroup)
    }

    // Handle room pre-configuration
    const handleRoomPreConfig = (roomName: string, selectedAgents: { [agentId: string]: Agent }, debateMode: boolean) => {
        const agentsList = Object.values(selectedAgents)
        setPreConfiguredRoom({
            roomName,
            selectedAgents: agentsList,
            debateMode
        })
        // Reset to default (not override) - Room Team if agents, otherwise All Agents
        setIsOverride(false)
        if (agentsList.length > 0) {
            setSelectedGroup(BUILTIN_GROUP_ROOM_TEAM)
        } else {
            setSelectedGroup(BUILTIN_GROUP_ALL_AGENTS)
        }
        setRoomSettingsOpen(false)
        banner.success('Room settings saved')
    }

    // Open room settings dialog
    const handleOpenRoomSettings = () => {
        loadAvailableAgents()
        setRoomSettingsOpen(true)
    }

    const handleSubmit = async (value: string) => {
        if (!value.trim()) {
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
            
            // Create room with pre-configured settings if available
            const options = {
                ...(preConfiguredRoom ? {
                    roomName: preConfiguredRoom.roomName || undefined,
                    selectedAgents: preConfiguredRoom.selectedAgents,
                    debateMode: preConfiguredRoom.debateMode
                } : {}),
                targetGroup: selectedGroup  // Always pass the selected group for the first message
            }
            
            const success = await createAndNavigate(value, options)
            
            if (success) {
                // RoomChatInput clears its own state after submission
                setPreConfiguredRoom(null) // Clear pre-configured settings after successful creation
            } else {
                throw new Error('Failed to create room')
            }
        } catch (error) {
            console.error('Error creating room:', error)
            setHasError(true)
            banner.error('Failed to start chat')
        }
    }

    const handleQuickStart = (prompt: string) => {
        setQuickStartValue(prompt)
    }

    const handleClearQuickStart = () => {
        setQuickStartValue("")
    }

    const handleRetry = () => {
        // Retry functionality - user needs to re-enter the message
        setHasError(false)
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
                            onClick={handleRetry} 
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
            {/* Fixed Header - Same position as room page */}
            <header className="shrink-0 w-full max-w-4xl mx-auto px-4 sm:px-6">
                <div className="flex items-center justify-between py-4 bg-background/95 backdrop-blur supports-backdrop-filter:bg-background/60">
                    <div className="flex items-center gap-3">
                        {/* Pre-configured room indicator - Same style as room page */}
                        <div className="space-y-1">
                            {preConfiguredRoom?.roomName && (
                                <h1 className="text-xl font-semibold">{preConfiguredRoom.roomName}</h1>
                            )}
                            
                            {/* Room team / Debate mode info */}
                            {preConfiguredRoom && (preConfiguredRoom.selectedAgents.length > 0 || preConfiguredRoom.debateMode) && (
                                <div className="flex items-center gap-2 flex-wrap">
                                    {/* Show agent count if room has agents */}
                                    {preConfiguredRoom.selectedAgents.length > 0 && (
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
                                    )}
                                    
                                    {preConfiguredRoom.debateMode && (
                                        <div className="flex items-center gap-1">
                                            <div className="w-2 h-2 bg-purple-500 rounded-full animate-pulse" />
                                            <span className="text-xs text-purple-600 dark:text-purple-400 font-medium">
                                                Debate Mode
                                            </span>
                                        </div>
                                    )}
                                </div>
                            )}
                        </div>
                    </div>
                    
                    {/* Settings Button - Same position as room page */}
                    <div className="flex items-center gap-2">
                        <TooltipProvider>
                            <Tooltip>
                                <TooltipTrigger asChild>
                                    <Button 
                                        variant="ghost" 
                                        size="icon" 
                                        onClick={handleOpenRoomSettings}
                                    >
                                        <Settings className="h-5 w-5 icon-neutral" />
                                    </Button>
                                </TooltipTrigger>
                                <TooltipContent>
                                    <p>Configure room settings</p>
                                </TooltipContent>
                            </Tooltip>
                        </TooltipProvider>
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
                                "font-bold font-spaceGrotesk text-[hsl(var(--color-hybro-hy-strong))] dark:text-[hsl(var(--color-hybro-hy))] mr-2",
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
                            <span className="ml-2 text-foreground inline-block text-3xl skew-x-150 scale-125 rotate-6">!</span>
                        </h1>
                        <p className="text-muted-foreground">
                            What would you like to work on today?
                        </p>
                    </div>
                    
                    {/* Creating state */}
                    {creating && (
                        <div className="flex items-center justify-center mb-6">
                            <div className="flex items-center gap-3 px-4 py-2 bg-primary/10 rounded-lg border border-primary/20">
                                <Loader2 className="h-4 w-4 animate-spin text-primary" />
                                <span className="text-sm">Finding the best agents for you...</span>
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
                            groups={groups}
                            loadingGroups={loadingGroups}
                            selectedGroup={selectedGroup}
                            onGroupChange={handleGroupChange}
                            roomAgentCount={preConfiguredRoom?.selectedAgents.length || 0}
                            onManageGroups={handleManageGroups}
                            isOverride={isOverride}
                            onClearOverride={handleClearOverride}
                            externalValue={quickStartValue}
                            onExternalValueConsumed={handleClearQuickStart}
                        />
                    </div>

                    {/* Quick start templates */}
                    <div className="space-y-3">
                        <p className="text-center text-sm text-muted-foreground">
                            Or start with a template
                        </p>
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
                            Just start typing — we&apos;ll find the best agents for your task
                        </p>
                    </div>
                </div>
            </div>

            {/* Group Management Modal */}
            <GroupManagementModal
                open={groupManagementOpen}
                onOpenChange={setGroupManagementOpen}
                groups={groups}
                onGroupsChange={handleGroupsChange}
                availableAgents={availableAgents}
                loadingAgents={loadingAgents}
                userId={user?.id || ''}
                getToken={getToken}
            />

            {/* Room Settings Dialog */}
            <Dialog open={roomSettingsOpen} onOpenChange={setRoomSettingsOpen}>
                <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto bg-background/80 backdrop-blur-md border shadow-lg">
                    <DialogHeader>
                        <DialogTitle>Room Settings</DialogTitle>
                    </DialogHeader>
                    <div className="mt-4">
                        <RoomSettingForm
                            onSubmit={handleRoomPreConfig}
                            availableAgents={availableAgents}
                            loadingAgents={loadingAgents}
                            isEditing={false}
                            requireRoomName={false}
                            submitButtonText="Save Settings"
                            initialData={preConfiguredRoom ? {
                                roomName: preConfiguredRoom.roomName,
                                selectedAgents: Object.fromEntries(
                                    preConfiguredRoom.selectedAgents.map(a => [a.agent_id, a.agent_card.name])
                                ),
                                debateMode: preConfiguredRoom.debateMode
                            } : null}
                        />
                    </div>
                </DialogContent>
            </Dialog>
        </div>
    )
}
