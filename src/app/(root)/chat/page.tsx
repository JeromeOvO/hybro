"use client"

import { useState, useEffect } from "react"
import { useUser, useClerk, useAuth } from "@clerk/nextjs"
import { ChatInput } from "@/components/chat-input"
import { toast } from "sonner"
import { 
    Loader2, 
    AlertCircle, 
    RefreshCw, 
    FlaskConical,
    Code,
    PenLine,
    BarChart3,
    Sparkles,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { useChatRoomCreation } from "@/hooks/useChatRoomCreation"
import { isWaitlistEnabled } from "@/lib/utils"
import { listAgentGroups } from "@/lib/api/agent-group"
import type { AgentGroup } from "@/lib/types/agent-group"
import { BUILTIN_GROUP_ALL_AGENTS } from "@/lib/types/agent-group"

const quickStartTemplates = [
    { icon: FlaskConical, label: "Research", prompt: "Help me research " },
    { icon: Code, label: "Code", prompt: "Help me write code for " },
    { icon: PenLine, label: "Write", prompt: "Help me write " },
    { icon: BarChart3, label: "Analyze", prompt: "Help me analyze " },
]

export default function ChatPage() {
    const { user, isLoaded } = useUser()
    const { getToken } = useAuth()
    const [input, setInput] = useState("")
    const [hasError, setHasError] = useState(false)
    const [selectedGroup, setSelectedGroup] = useState<string>(BUILTIN_GROUP_ALL_AGENTS)
    const [groups, setGroups] = useState<AgentGroup[]>([])
    const [loadingGroups, setLoadingGroups] = useState(false)
    const { openWaitlist } = useClerk()
    
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

    const handleSubmit = async (value: string, targetGroup?: string) => {
        if (!value.trim()) {
            toast.error("Please enter a message")
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
            
            // Create room with the selected group
            // For new chats, we always use "All Agents" as the default
            const success = await createAndNavigate(value)
            
            if (success) {
                setInput("")
            } else {
                throw new Error('Failed to create room')
            }
        } catch (error) {
            console.error('Error creating room:', error)
            setHasError(true)
            toast.error('Failed to start chat')
        }
    }

    const handleQuickStart = (prompt: string) => {
        setInput(prompt)
    }

    const handleRetry = () => {
        if (input.trim()) {
            handleSubmit(input, selectedGroup)
        }
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
        <div className="flex flex-col h-full">
            <div className="flex-1 flex items-center justify-center p-4">
                <div className="w-full max-w-3xl">
                    {/* Header */}
                    <div className="text-center mb-8">
                        <h1 className="text-4xl font-bold mb-2">
                            <span className="bg-gradient-to-r from-[hsl(var(--color-hybro-hy))] to-[hsl(var(--color-hybro-bro))] bg-clip-text text-transparent">
                                HYBRO
                            </span>
                            <span className="ml-2 text-foreground">AI</span>
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
                        <ChatInput
                            onSubmit={handleSubmit}
                            disabled={creating}
                            placeholder={creating ? "Starting chat..." : "Ask anything..."}
                            value={input}
                            onChange={setInput}
                            showGroupSelector={true}
                            groups={groups}
                            loadingGroups={loadingGroups}
                            selectedGroup={selectedGroup}
                            onGroupChange={setSelectedGroup}
                            roomAgentCount={0}
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
        </div>
    )
}
