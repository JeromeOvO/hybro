'use client'

import React, { useState, useEffect, useCallback, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import { 
  Loader2, 
  CheckCircle, 
  XCircle, 
  Clock, 
  AlertTriangle, 
  KeyRound,
  Sparkles
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { type TaskState, isTerminalState, PENDING_STATES, INTERACTIVE_STATES } from '@/lib/types/sse'

interface TaskStatusMessageProps {
  internalId: string
  agentName: string
  initialStatus: TaskState
  content?: string | null
  error?: string | null
  statusMessage?: string | null
  onComplete?: (content: string) => void
  onError?: (error: string) => void
}

function MarkdownContent({ content }: { content: string }) {
  const processedContent = content.replace(
    /<@([^|]+)\|([^>]+)>/g,
    '<span class="room-mention" data-id="$1" data-name="$2" title="Agent: $2">@$2</span>'
  )

  return (
    <div className="prose prose-sm max-w-none leading-relaxed prose-p:text-inherit prose-headings:text-inherit prose-li:text-inherit prose-strong:text-inherit prose-em:text-inherit [&_.room-mention]:mx-1">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={{
          span: ({ className, children, ...props }) => {
            if (className === 'room-mention') {
              return (
                <span
                  className="room-mention mx-1"
                  {...props}
                >
                  {children}
                </span>
              )
            }
            return <span className={className} {...props}>{children}</span>
          },
          code: ({ className, children, ...props }) => {
            const match = /language-(\w+)/.exec(className || '')
            const isInline = !match
            return isInline ? (
              <code className="bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-200 px-1.5 py-0.5 rounded text-sm font-mono" {...props}>
                {children}
              </code>
            ) : (
              <pre className="bg-slate-100 dark:bg-slate-900 text-slate-700 dark:text-slate-200 p-3 rounded-md overflow-x-auto border border-slate-200 dark:border-slate-700">
                <code className={className} {...props}>
                  {children}
                </code>
              </pre>
            )
          },
          p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
          ul: ({ children }) => <ul className="mb-2 ml-4 list-disc">{children}</ul>,
          ol: ({ children }) => <ol className="mb-2 ml-4 list-decimal">{children}</ol>,
          li: ({ children }) => <li className="mb-1">{children}</li>,
          h1: ({ children }) => <h1 className="text-lg font-bold mb-2">{children}</h1>,
          h2: ({ children }) => <h2 className="text-base font-bold mb-2">{children}</h2>,
          h3: ({ children }) => <h3 className="text-sm font-bold mb-1">{children}</h3>,
          h4: ({ children }) => <h4 className="text-sm font-semibold mb-1">{children}</h4>,
          h5: ({ children }) => <h5 className="text-xs font-semibold mb-1">{children}</h5>,
          h6: ({ children }) => <h6 className="text-xs font-medium mb-1">{children}</h6>,
        }}
      >
        {processedContent}
      </ReactMarkdown>
    </div>
  )
}

export function TaskStatusMessage({
  internalId,
  agentName,
  initialStatus,
  content: initialContent,
  error: initialError,
  statusMessage: initialStatusMessage,
  onComplete,
  onError,
}: TaskStatusMessageProps) {
  const [status, setStatus] = useState<TaskState>(initialStatus)
  const [content, setContent] = useState<string | null>(initialContent || null)
  const [error, setError] = useState<string | null>(initialError || null)
  const [statusMessage, setStatusMessage] = useState<string | null>(initialStatusMessage || null)
  const [elapsed, setElapsed] = useState(0)
  
  // Track if we've already processed this update (deduplication)
  const processedStates = useRef<Set<string>>(new Set())

  // Handle SSE or poll updates with deduplication
  const handleUpdate = useCallback((data: {
    status: TaskState
    content?: string
    error?: string
    status_message?: string
  }) => {
    // Deduplicate by status (don't re-render for same state)
    const stateKey = `${data.status}-${data.content || ""}-${data.error || ""}`
    if (processedStates.current.has(stateKey)) {
      return
    }
    processedStates.current.add(stateKey)
    
    setStatus(data.status)
    if (data.content) {
      setContent(data.content)
      onComplete?.(data.content)
    }
    if (data.error) {
      setError(data.error)
      onError?.(data.error)
    }
    if (data.status_message) {
      setStatusMessage(data.status_message)
    }
  }, [onComplete, onError])

  // Update from props when they change (SSE updates)
  useEffect(() => {
    if (initialStatus !== status) {
      handleUpdate({
        status: initialStatus,
        content: initialContent || undefined,
        error: initialError || undefined,
        status_message: initialStatusMessage || undefined,
      })
    }
  }, [initialStatus, initialContent, initialError, initialStatusMessage, status, handleUpdate])

  // Elapsed time counter (only for non-terminal states)
  useEffect(() => {
    if (isTerminalState(status)) return
    
    const interval = setInterval(() => setElapsed(e => e + 1), 1000)
    return () => clearInterval(interval)
  }, [status])

  const formatTime = (s: number) => {
    if (s < 60) return `${s}s`
    if (s < 3600) return `${Math.floor(s/60)}m ${s%60}s`
    return `${Math.floor(s/3600)}h ${Math.floor((s%3600)/60)}m`
  }

  // Completed state
  if (status === "completed" && content) {
    return (
      <div className="flex gap-3 w-full animate-in fade-in slide-in-from-bottom-2 duration-300">
        {/* Avatar */}
        <div className="w-8 h-8 rounded-full flex items-center justify-center font-semibold border-2 shrink-0 bg-gradient-to-br from-green-100 to-emerald-100 dark:from-green-900 dark:to-emerald-900 border-green-300 dark:border-green-700">
          <CheckCircle className="h-4 w-4 text-green-600 dark:text-green-300" />
        </div>

        {/* Content */}
        <div className="flex-1 max-w-[calc(100%-3rem)] rounded-lg p-4 shadow-sm border border-green-200 dark:border-green-800 bg-gradient-to-br from-green-50/50 to-emerald-50/30 dark:from-green-950/50 dark:to-emerald-950/30 message-bubble">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium text-green-700 dark:text-green-300">
              {agentName}
            </span>
            <span className="text-xs text-muted-foreground flex items-center gap-1">
              <CheckCircle className="w-3 h-3" />
              Completed in {formatTime(elapsed)}
            </span>
          </div>
          <div className="text-green-800 dark:text-green-200">
            <MarkdownContent content={content} />
          </div>
        </div>
      </div>
    )
  }

  // Failed/Rejected/Canceled states
  if (status === "failed" || status === "rejected" || status === "canceled") {
    const titles: Record<string, string> = {
      failed: "Task failed",
      rejected: "Task was rejected",
      canceled: "Task was canceled",
    }
    
    return (
      <div className="flex gap-3 w-full animate-in fade-in slide-in-from-bottom-2 duration-300">
        {/* Avatar */}
        <div className="w-8 h-8 rounded-full flex items-center justify-center font-semibold border-2 shrink-0 bg-gradient-to-br from-red-100 to-rose-100 dark:from-red-900 dark:to-rose-900 border-red-300 dark:border-red-700">
          <XCircle className="h-4 w-4 text-red-600 dark:text-red-300" />
        </div>

        {/* Content */}
        <div className="flex-1 max-w-[calc(100%-3rem)] rounded-lg p-4 shadow-sm border border-red-200 dark:border-red-800 bg-gradient-to-br from-red-50/50 to-rose-50/30 dark:from-red-950/50 dark:to-rose-950/30 message-bubble">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium text-red-700 dark:text-red-300">
              {agentName}
            </span>
            <span className="text-xs text-red-600 dark:text-red-400">
              {titles[status]}
            </span>
          </div>
          {error && (
            <div className="text-sm text-red-700 dark:text-red-300">
              <MarkdownContent content={error} />
            </div>
          )}
        </div>
      </div>
    )
  }

  // Input required state
  if (status === "input_required") {
    return (
      <div className="flex gap-3 w-full animate-in fade-in slide-in-from-bottom-2 duration-300">
        {/* Avatar */}
        <div className="w-8 h-8 rounded-full flex items-center justify-center font-semibold border-2 shrink-0 bg-gradient-to-br from-yellow-100 to-amber-100 dark:from-yellow-900 dark:to-amber-900 border-yellow-300 dark:border-yellow-700">
          <AlertTriangle className="h-4 w-4 text-yellow-600 dark:text-yellow-300" />
        </div>

        {/* Content */}
        <div className="flex-1 max-w-[calc(100%-3rem)] rounded-lg p-4 shadow-sm border border-yellow-200 dark:border-yellow-800 bg-gradient-to-br from-yellow-50/50 to-amber-50/30 dark:from-yellow-950/50 dark:to-amber-950/30 message-bubble">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium text-yellow-700 dark:text-yellow-300">
              {agentName}
            </span>
            <span className="text-xs text-yellow-600 dark:text-yellow-400 font-medium">
              Input required
            </span>
          </div>
          <div className="text-sm text-yellow-700 dark:text-yellow-300">
            <MarkdownContent
              content={statusMessage || "The agent needs additional information to continue."}
            />
          </div>
          <p className="text-xs text-yellow-500 dark:text-yellow-400 mt-2 flex items-center gap-1">
            <Clock className="w-3 h-3" />
            {formatTime(elapsed)} elapsed
          </p>
        </div>
      </div>
    )
  }

  // Auth required state
  if (status === "auth_required") {
    return (
      <div className="flex gap-3 w-full animate-in fade-in slide-in-from-bottom-2 duration-300">
        {/* Avatar */}
        <div className="w-8 h-8 rounded-full flex items-center justify-center font-semibold border-2 shrink-0 bg-gradient-to-br from-orange-100 to-amber-100 dark:from-orange-900 dark:to-amber-900 border-orange-300 dark:border-orange-700">
          <KeyRound className="h-4 w-4 text-orange-600 dark:text-orange-300" />
        </div>

        {/* Content */}
        <div className="flex-1 max-w-[calc(100%-3rem)] rounded-lg p-4 shadow-sm border border-orange-200 dark:border-orange-800 bg-gradient-to-br from-orange-50/50 to-amber-50/30 dark:from-orange-950/50 dark:to-amber-950/30 message-bubble">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium text-orange-700 dark:text-orange-300">
              {agentName}
            </span>
            <span className="text-xs text-orange-600 dark:text-orange-400 font-medium">
              Authentication required
            </span>
          </div>
          <div className="text-sm text-orange-700 dark:text-orange-300">
            <MarkdownContent content={statusMessage || "Please authenticate to continue."} />
          </div>
          <p className="text-xs text-orange-500 dark:text-orange-400 mt-2 flex items-center gap-1">
            <Clock className="w-3 h-3" />
            {formatTime(elapsed)} elapsed
          </p>
        </div>
      </div>
    )
  }

  // Working/Submitted states (default - in progress)
  return (
    <div className="flex gap-3 w-full animate-in fade-in slide-in-from-bottom-2 duration-300">
      {/* Avatar matching agent bubble style */}
      <div className="w-8 h-8 rounded-full flex items-center justify-center font-semibold border-2 shrink-0 bg-gradient-to-br from-blue-100 to-indigo-100 dark:from-blue-900 dark:to-indigo-900 border-blue-300 dark:border-blue-700">
        <Sparkles className="h-4 w-4 text-blue-600 dark:text-blue-300 animate-pulse" />
      </div>

      {/* Message content */}
      <div className="flex-1 max-w-[calc(100%-3rem)] rounded-lg p-4 shadow-sm border border-blue-200 dark:border-blue-800 bg-gradient-to-br from-blue-50/50 to-indigo-50/30 dark:from-blue-950/50 dark:to-indigo-950/30 message-bubble">
        {/* Header */}
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs font-medium text-blue-700 dark:text-blue-300">
            {agentName}
          </span>
          <span className="text-xs text-muted-foreground">
            Working...
          </span>
        </div>

        {/* Animated content */}
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <Loader2 className="w-4 h-4 animate-spin text-blue-600 dark:text-blue-400" />
            <span className="text-sm text-blue-600 dark:text-blue-300 font-medium">
              Processing your request
            </span>
          </div>

          {statusMessage && (
            <div className="text-sm text-blue-600 dark:text-blue-300">
              <MarkdownContent content={statusMessage} />
            </div>
          )}
          
          {/* Elapsed time */}
          <p className="text-xs text-blue-500 dark:text-blue-400 flex items-center gap-1">
            <Clock className="w-3 h-3" />
            {formatTime(elapsed)} elapsed
          </p>
        </div>
      </div>
    </div>
  )
}
