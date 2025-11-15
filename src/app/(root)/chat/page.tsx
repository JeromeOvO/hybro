"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { useUser } from "@clerk/nextjs"
import { ChatInput } from "@/components/chat-input"
import { sendMessage } from "@/lib/api"
import { toast } from "sonner"
import { Loader2, AlertCircle, RefreshCw } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { WaitlistDialog } from "@/components/waitlist-dialog"
import type { ChatRequest } from "@/lib/types"

export default function ChatPage() {
    const router = useRouter()
    const { user, isLoaded } = useUser()
    const [input, setInput] = useState("")
    const [isSubmitting, setIsSubmitting] = useState(false)
    const [hasError, setHasError] = useState(false)
    const [isWaitlistOpen, setIsWaitlistOpen] = useState(false)

    const handleSubmit = async (value: string) => {
        if (!value.trim()) {
            toast.error("Please enter a message")
            return
        }

        if (!user?.id) {
            setIsWaitlistOpen(true)
            return
        }

        try {
            setIsSubmitting(true)
            setHasError(false)
            
            const request: ChatRequest = {
                user_name: user.id,
                user_input: value
            }

            const response = await sendMessage(request)
            
            if (response.session_id) {
                // Navigate directly to session page, no need to pass additional information
                router.push(`/chat/${response.session_id}`)
            } else {
                throw new Error('Invalid response: missing session_id')
            }
        } catch (error) {
            console.error('Error sending message:', error)
            setHasError(true)
            toast.error('Failed to send message')
        } finally {
            setIsSubmitting(false)
        }
    }

    const handleRetry = () => {
        if (input.trim()) {
            handleSubmit(input)
        }
    }

    if (!isLoaded) {
        return (
            <div className="flex items-center justify-center h-full">
                <Loader2 className="h-8 w-8 animate-spin icon-action" />
            </div>
        )
    }

    if (hasError) {
        return (
            <div className="flex items-center justify-center h-full p-4">
                <Card className="w-full max-w-md">
                    <CardHeader className="text-center">
                        <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-destructive/10">
                            <AlertCircle className="h-6 w-6 icon-error" />
                        </div>
                        <CardTitle className="text-destructive">Server Error</CardTitle>
                        <CardDescription>
                            Failed to connect to the server. Please try again.
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <Button 
                            onClick={handleRetry} 
                            className="w-full"
                            disabled={isSubmitting}
                        >
                            {isSubmitting ? (
                                <>
                                    <Loader2 className="mr-2 h-4 w-4 animate-spin icon-action" />
                                    Retrying...
                                </>
                            ) : (
                                <>
                                    <RefreshCw className="mr-2 h-4 w-4 icon-action" />
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
                <div className="w-full max-w-4xl">
                    <div className="text-center mb-8">
                        <h1 className="text-4xl font-bold mb-4">
                            <span className="text-[hsl(var(--color-hybro-hy))]">HY</span>
                            <span className="text-[hsl(var(--color-hybro-bro))]">BRO</span>
                            <span className="ml-2">AI</span>
                        </h1>
                    </div>
                    
                    {isSubmitting && (
                        <div className="flex items-center justify-center mb-6">
                            <div className="flex items-center gap-3 px-4 py-2 bg-muted rounded-lg">
                                <Loader2 className="h-4 w-4 animate-spin icon-action" />
                                <span className="text-sm">Creating new session...</span>
                            </div>
                        </div>
                    )}
                    
                    <ChatInput
                        onSubmit={handleSubmit}
                        disabled={isSubmitting}
                        placeholder={isSubmitting ? "Creating session..." : "Type your message here..."}
                        value={input}
                        onChange={setInput}
                    />
                </div>
            </div>

            <WaitlistDialog 
                open={isWaitlistOpen} 
                onOpenChange={setIsWaitlistOpen}
                showTrigger={false}
            />
        </div>
    )
}
