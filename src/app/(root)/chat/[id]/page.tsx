"use client"

import { useState, useEffect, useRef, useCallback } from "react"
import { useParams } from "next/navigation"
import { useUser, useClerk } from "@clerk/nextjs"
import { ChatSession } from "@/components/chat-session"
import { ChatInput } from "@/components/chat-input"
import { toast } from "sonner"
import { getBaseTasksBySessionId, sendMessage, getMetaTasksByParentId } from "@/lib/api"
import { useTaskPolling } from "@/hooks/useTaskPolling"
import type { 
  MessageData, 
  BaseTask,
  MetaTask,
  ChatRequest,
  Role,
  Message,
  TextPart
} from "@/lib/types"
import { isWaitlistEnabled } from "@/lib/utils"

export default function ChatSessionPage() {
  const params = useParams()
  const { user, isLoaded } = useUser()
  const sessionId = params.id as string
  const { openWaitlist } = useClerk()
  const [messages, setMessages] = useState<MessageData[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const baseTasksRef = useRef<BaseTask[]>([])

  const { 
    startPolling, 
    stopPolling,
    stopAllPolling, 
    needsPolling,
  } = useTaskPolling()

  const convertMessageToMessageData = useCallback((message: Message, taskId: string): MessageData => {
    const textParts = message.parts.filter(part => part.kind === 'text') as TextPart[]
    const content = textParts.length > 0 ? textParts[0].text : ''
    
    return {
      id: message.messageId,
      role: message.role as Role,
      content,
      timestamp: new Date(),
      taskId,
      messageType: 'text'
    }
  }, [])

  const shouldCreateWorkflowMessage = useCallback((baseTask: BaseTask): boolean => {
    return baseTask.task.status.state === 'working' || 
           baseTask.task.status.state === 'submitted'
  }, [])

  const createWorkflowMessage = useCallback(async (baseTask: BaseTask): Promise<MessageData> => {
    let metaTasks: MetaTask[] = []
    
    try {
      const metaTasksResponse = await getMetaTasksByParentId(baseTask.task_id)
      metaTasks = metaTasksResponse.meta_tasks || []
    } catch (error) {
      console.error('Error fetching meta tasks:', error)
    }

    return {
      id: `workflow-${baseTask.task_id}`,
      role: 'agent',
      content: 'Processing your request with multiple agents...',
      timestamp: new Date(),
      taskId: baseTask.task_id,
      messageType: 'workflow',
      workflowData: {
        baseTask,
        metaTasks
      }
    }
  }, [])

  const handleWorkflowComplete = useCallback((baseTask: BaseTask) => {
    console.log('Workflow completed for baseTask:', baseTask.task_id)
    
    // Create new message to display final result
    const agentMessages = baseTask.task.history?.filter(msg => msg.role === 'agent') || []
    const latestAgentMessage = agentMessages[agentMessages.length - 1]
    
    let resultContent = 'Task completed successfully!'
    if (latestAgentMessage?.parts) {
      const textParts = latestAgentMessage.parts.filter(part => part.kind === 'text') as TextPart[]
      if (textParts.length > 0) {
        resultContent = textParts[0].text
      }
    }
    
    const resultMessage: MessageData = {
      id: `result-${baseTask.task_id}-${Date.now()}`,
      role: 'agent',
      content: resultContent,
      timestamp: new Date(),
      taskId: baseTask.task_id,
      messageType: 'text'
    }

    // Add result message to message list
    setMessages(prev => [...prev, resultMessage])
    
    // Stop polling
    stopPolling(baseTask.task_id)
    
    toast.success('Workflow completed successfully!')
  }, [stopPolling])

  // Convert BaseTask to MessageData, including thinking messages and workflow messages
  const convertBaseTasksToMessages = useCallback(async (baseTasks: BaseTask[]): Promise<MessageData[]> => {
    const allMessages: MessageData[] = []
    
    for (const baseTask of baseTasks) {
      if (baseTask.task.history) {
        // Add actual messages
        baseTask.task.history.forEach(message => {
          const messageData = convertMessageToMessageData(message, baseTask.task_id)
          allMessages.push(messageData)
        })
      }

      // Check if workflow message should be created
      if (shouldCreateWorkflowMessage(baseTask)) {
        const workflowMessage = await createWorkflowMessage(baseTask)
        allMessages.push(workflowMessage)
      }
    }

    return allMessages
  }, [convertMessageToMessageData, needsPolling, shouldCreateWorkflowMessage, createWorkflowMessage])

  // Handle task polling updates
  const handleTaskUpdate = useCallback(async (baseTask: BaseTask) => {
    console.log('Task updated:', baseTask.task_id, baseTask.task.status.state)
    
    baseTasksRef.current = baseTasksRef.current.map(task => 
      task.task_id === baseTask.task_id ? baseTask : task
    )

    // Regenerate messages
    const messageData = await convertBaseTasksToMessages(baseTasksRef.current)
    setMessages(messageData)
  }, [convertBaseTasksToMessages])

  // Handle task completion
  const handleTaskComplete = useCallback(async (baseTask: BaseTask) => {
    console.log('Task completed:', baseTask.task_id)
    
    // Update baseTasksRef
    baseTasksRef.current = baseTasksRef.current.map(task => 
      task.task_id === baseTask.task_id ? baseTask : task
    )

    // Regenerate messages
    const messageData = await convertBaseTasksToMessages(baseTasksRef.current)
    setMessages(messageData)
  }, [convertBaseTasksToMessages])

  // Handle polling errors
  const handleTaskError = useCallback((error: string) => {
    console.error('Polling error:', error)
    toast.error(error)
  }, [])

  // Load session messages
  const loadSessionMessages = useCallback(async () => {
    if (!sessionId) return

    try {
      setIsLoading(true)
      const response = await getBaseTasksBySessionId(sessionId)
      const baseTasks = response.base_tasks || []
      baseTasksRef.current = baseTasks

      // Convert to message data
      const messageData = await convertBaseTasksToMessages(baseTasks)
      setMessages(messageData)

      // Start polling incomplete tasks
      baseTasks.forEach(baseTask => {
        if (needsPolling(baseTask)) {
          startPolling(
            baseTask.task_id,
            handleTaskUpdate,
            handleTaskComplete,
            handleTaskError
          )
        }
      })
    } catch (error) {
      console.error('Error loading session messages:', error)
      toast.error('Failed to load session messages')
    } finally {
      setIsLoading(false)
    }
  }, [sessionId, convertBaseTasksToMessages, needsPolling, startPolling, handleTaskUpdate, handleTaskComplete, handleTaskError])

  // Send message
  const handleSendMessage = async (content: string) => {
    if (!user || isSubmitting) return

    try {
      setIsSubmitting(true)
      
      // Add user message to UI
      const userMessage: MessageData = {
        id: `user-${Date.now()}`,
        role: 'user',
        content,
        timestamp: new Date(),
        messageType: 'text'
      }
      
      setMessages(prev => [...prev, userMessage])

      // Send message to backend
      const chatRequest: ChatRequest = {
        user_name: user.id,
        user_input: content,
        session_id: sessionId
      }

      await sendMessage(chatRequest)
      
      // Reload messages
      await loadSessionMessages()
      
    } catch (error) {
      console.error('Error sending message:', error)
      toast.error('Failed to send message')
    } finally {
      setIsSubmitting(false)
    }
  }

  // Initialize loading
  useEffect(() => {
    if (isLoaded && user && sessionId) {
      loadSessionMessages()
    }
  }, [isLoaded, user, sessionId, loadSessionMessages])

  // Cleanup polling
  useEffect(() => {
    return () => {
      stopAllPolling()
    }
  }, [stopAllPolling])

  if (!isLoaded) {
    return <div>Loading...</div>
  }

  if (isLoaded && !user?.id) {
    if (isWaitlistEnabled()) {
      openWaitlist()
    } else {
      // When waitlist is disabled, redirect to sign-in
      if (typeof window !== "undefined") {
        window.location.href = "/sign-in"
      }
    }
    return
  }

  return (
    <div className="flex flex-col h-screen bg-background">
      <div className="flex-1 overflow-hidden">
        <ChatSession
          messages={messages}
          isLoading={isLoading}
          onWorkflowComplete={handleWorkflowComplete}
          showHeader={false}
          className="w-full max-w-4xl mx-auto px-4 sm:px-6"
        />
      </div>
      
      <div className="bg-background p-4">
        <div className="max-w-4xl mx-auto px-4 sm:px-6">
          <ChatInput
            onSubmit={handleSendMessage}
            disabled={isSubmitting}
            placeholder="Type your message..."
          />
        </div>
      </div>
    </div>
  )
}