'use client'

import { useState, useEffect, useCallback, useRef } from 'react'
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
  Sparkles,
  ChevronDown,
  ChevronUp
} from 'lucide-react'
import { type TaskState, isTerminalState } from '@/lib/types/sse'
import { elapsedSeconds, formatElapsedTime } from '@/lib/time'

interface TaskStatusMessageProps {
  internalId: string
  agentId?: string
  agentName: string
  initialStatus: TaskState
  content?: string | null
  error?: string | null
  statusMessage?: string | null
  stepNumber?: number // Current step number (1-indexed)
  totalSteps?: number // Total number of steps
  taskContent?: string // The task description being processed
  taskCreatedAt?: string // Task creation timestamp for elapsed time calculation
  onComplete?: (content: string) => void
  onError?: (error: string) => void
}

// Simple step indicator - shows "Step X / Y" consistently
function StepIndicator({ 
  stepNumber, 
  totalSteps 
}: { 
  stepNumber?: number
  totalSteps?: number
}) {
  if (!stepNumber || !totalSteps || totalSteps <= 0) {
    return null
  }
  
  return (
    <span className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-current/10">
      Step {stepNumber} / {totalSteps}
    </span>
  )
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

// Threshold for collapsible content (matching AgentMessageBubble)
const LONG_CONTENT_THRESHOLD = 500

/**
 * Collapse/expand toggle button for long content
 */
function CollapseToggle({
  isExpanded,
  onToggle,
  colorClass,
  toggleRef,
}: {
  isExpanded: boolean
  onToggle: () => void
  colorClass: string
  toggleRef?: React.RefObject<HTMLButtonElement | null>
}) {
  return (
    <button
      ref={toggleRef}
      onClick={onToggle}
      className={`flex items-center gap-1 text-xs mt-3 font-medium transition-colors ${colorClass} hover:opacity-80`}
    >
      {isExpanded ? (
        <>
          <ChevronUp className="h-3.5 w-3.5" />
          Show less
        </>
      ) : (
        <>
          <ChevronDown className="h-3.5 w-3.5" />
          Show more
        </>
      )}
    </button>
  )
}

/**
 * Clickable agent avatar + name link (matches AgentMessageBubble pattern)
 */
function AgentLink({
  agentId,
  agentName,
  avatarChildren,
  avatarClassName,
  nameClassName,
}: {
  agentId?: string
  agentName: string
  avatarChildren: React.ReactNode
  avatarClassName: string
  nameClassName: string
}) {
  const inner = (
    <>
      <div className={avatarClassName} title={agentName}>
        {avatarChildren}
      </div>
      <span className={`text-xs font-semibold${agentId ? ' underline-offset-2 hover:underline' : ''} ${nameClassName}`}>
        {agentName}
      </span>
    </>
  )

  if (agentId) {
    return (
      <a
        href={`/c/agents/${agentId}`}
        target="_blank"
        rel="noopener noreferrer"
        className="flex items-center gap-2 hover:opacity-80 transition-opacity"
      >
        {inner}
      </a>
    )
  }

  return <div className="flex items-center gap-2">{inner}</div>
}

export function TaskStatusMessage({
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  internalId,
  agentId,
  agentName,
  initialStatus,
  content: initialContent,
  error: initialError,
  statusMessage: initialStatusMessage,
  stepNumber: initialStepNumber,
  totalSteps: initialTotalSteps,
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  taskContent,
  taskCreatedAt,
  onComplete,
  onError,
}: TaskStatusMessageProps) {
  const [status, setStatus] = useState<TaskState>(initialStatus)
  const [content, setContent] = useState<string | null>(initialContent || null)
  const [error, setError] = useState<string | null>(initialError || null)
  const [statusMessage, setStatusMessage] = useState<string | null>(initialStatusMessage || null)
  const [stepNumber, setStepNumber] = useState<number | undefined>(initialStepNumber)
  const [totalSteps, setTotalSteps] = useState<number | undefined>(initialTotalSteps)
  
  // Calculate initial elapsed time from task creation timestamp using centralized utility
  const [elapsed, setElapsed] = useState(() => {
    if (isTerminalState(initialStatus)) return 0
    return elapsedSeconds(taskCreatedAt)
  })

  // Collapsible state for long content in terminal states
  const [isExpanded, setIsExpanded] = useState(false)
  const toggleButtonRef = useRef<HTMLButtonElement>(null)
  
  // Track if we've already processed this update (deduplication)
  const processedStates = useRef<Set<string>>(new Set())

  // Handle SSE or poll updates with deduplication
  const handleUpdate = useCallback((data: {
    status: TaskState
    content?: string
    error?: string
    status_message?: string
    step_number?: number
    total_steps?: number
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
    if (data.step_number !== undefined) {
      setStepNumber(data.step_number)
    }
    if (data.total_steps !== undefined) {
      setTotalSteps(data.total_steps)
    }
  }, [onComplete, onError])

  // Update from props when they change (SSE updates)
  useEffect(() => {
    if (initialStatus !== status || initialStepNumber !== stepNumber || initialTotalSteps !== totalSteps) {
      handleUpdate({
        status: initialStatus,
        content: initialContent || undefined,
        error: initialError || undefined,
        status_message: initialStatusMessage || undefined,
        step_number: initialStepNumber,
        total_steps: initialTotalSteps,
      })
    }
  }, [initialStatus, initialContent, initialError, initialStatusMessage, initialStepNumber, initialTotalSteps, status, stepNumber, totalSteps, handleUpdate])

  // Elapsed time counter (only for non-terminal states)
  useEffect(() => {
    if (isTerminalState(status)) return
    
    const interval = setInterval(() => setElapsed(e => e + 1), 1000)
    return () => clearInterval(interval)
  }, [status])

  // Toggle handler for collapse/expand with scroll stabilization
  const handleToggle = useCallback(() => {
    const next = !isExpanded
    const buttonEl = toggleButtonRef.current
    const container = buttonEl?.closest('[data-message-scroll-container="true"]') as HTMLElement | null
    const prevBottom = buttonEl?.getBoundingClientRect().bottom

    setIsExpanded(next)

    // Keep collapse from jumping; let expand naturally push content downward.
    if (buttonEl && container && !next && typeof prevBottom === 'number') {
      container.dataset.programmaticScroll = 'true'
      requestAnimationFrame(() => {
        const newBottom = buttonEl.getBoundingClientRect().bottom
        const delta = newBottom - prevBottom
        if (delta !== 0) {
          container.scrollTop += delta
        }
        requestAnimationFrame(() => {
          container.dataset.programmaticScroll = 'false'
        })
      })
    }
  }, [isExpanded])

  // Completed state
  if (status === "completed" && content) {
    const isLong = content.length > LONG_CONTENT_THRESHOLD
    return (
      <div className="flex w-full animate-in fade-in slide-in-from-bottom-2 duration-300">
        <div className="flex-1 rounded-xl p-4 shadow-sm border border-green-200 dark:border-green-800 bg-gradient-to-br from-green-50/50 to-emerald-50/30 dark:from-green-950/50 dark:to-emerald-950/30 message-bubble text-green-600 dark:text-green-400">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <AgentLink
                agentId={agentId}
                agentName={agentName}
                avatarClassName="w-6 h-6 rounded-full flex items-center justify-center font-semibold border shrink-0 bg-gradient-to-br from-green-100 to-emerald-100 dark:from-green-900 dark:to-emerald-900 border-green-300 dark:border-green-700"
                avatarChildren={<CheckCircle className="h-3 w-3 text-green-600 dark:text-green-300" />}
                nameClassName="text-green-700 dark:text-green-300"
              />
              <StepIndicator stepNumber={stepNumber} totalSteps={totalSteps} />
            </div>
            <span className="text-xs text-muted-foreground flex items-center gap-1">
              <CheckCircle className="w-3 h-3" />
              Completed in {formatElapsedTime(elapsed)}
            </span>
          </div>
          <div className={`text-green-800 dark:text-green-200${!isExpanded && isLong ? ' line-clamp-4' : ''}`}>
            <MarkdownContent content={content} />
          </div>
          {isLong && (
            <CollapseToggle
              isExpanded={isExpanded}
              onToggle={handleToggle}
              colorClass="text-green-600 dark:text-green-400"
              toggleRef={toggleButtonRef}
            />
          )}
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
    const isLong = (error?.length || 0) > LONG_CONTENT_THRESHOLD
    
    return (
      <div className="flex w-full animate-in fade-in slide-in-from-bottom-2 duration-300">
        <div className="flex-1 rounded-xl p-4 shadow-sm border border-red-200 dark:border-red-800 bg-gradient-to-br from-red-50/50 to-rose-50/30 dark:from-red-950/50 dark:to-rose-950/30 message-bubble text-red-600 dark:text-red-400">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <AgentLink
                agentId={agentId}
                agentName={agentName}
                avatarClassName="w-6 h-6 rounded-full flex items-center justify-center font-semibold border shrink-0 bg-gradient-to-br from-red-100 to-rose-100 dark:from-red-900 dark:to-rose-900 border-red-300 dark:border-red-700"
                avatarChildren={<XCircle className="h-3 w-3 text-red-600 dark:text-red-300" />}
                nameClassName="text-red-700 dark:text-red-300"
              />
              <StepIndicator stepNumber={stepNumber} totalSteps={totalSteps} />
            </div>
            <span className="text-xs text-red-600 dark:text-red-400">
              {titles[status]}
            </span>
          </div>
          {error && (
            <div className={`text-sm text-red-700 dark:text-red-300${!isExpanded && isLong ? ' line-clamp-4' : ''}`}>
              <MarkdownContent content={error} />
            </div>
          )}
          {isLong && (
            <CollapseToggle
              isExpanded={isExpanded}
              onToggle={handleToggle}
              colorClass="text-red-600 dark:text-red-400"
              toggleRef={toggleButtonRef}
            />
          )}
        </div>
      </div>
    )
  }

  // Input required state
  if (status === "input_required") {
    const inputContent = statusMessage || "The agent needs additional information to continue."
    const isLong = inputContent.length > LONG_CONTENT_THRESHOLD
    return (
      <div className="flex w-full animate-in fade-in slide-in-from-bottom-2 duration-300">
        <div className="flex-1 rounded-xl p-4 shadow-sm border border-yellow-200 dark:border-yellow-800 bg-gradient-to-br from-yellow-50/50 to-amber-50/30 dark:from-yellow-950/50 dark:to-amber-950/30 message-bubble text-yellow-600 dark:text-yellow-400">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <AgentLink
                agentId={agentId}
                agentName={agentName}
                avatarClassName="w-6 h-6 rounded-full flex items-center justify-center font-semibold border shrink-0 bg-gradient-to-br from-yellow-100 to-amber-100 dark:from-yellow-900 dark:to-amber-900 border-yellow-300 dark:border-yellow-700"
                avatarChildren={<AlertTriangle className="h-3 w-3 text-yellow-600 dark:text-yellow-300" />}
                nameClassName="text-yellow-700 dark:text-yellow-300"
              />
              <StepIndicator stepNumber={stepNumber} totalSteps={totalSteps} />
            </div>
            <span className="text-xs text-yellow-600 dark:text-yellow-400 font-medium">
              Input required
            </span>
          </div>
          <div className={`text-sm text-yellow-700 dark:text-yellow-300${!isExpanded && isLong ? ' line-clamp-4' : ''}`}>
            <MarkdownContent content={inputContent} />
          </div>
          {isLong && (
            <CollapseToggle
              isExpanded={isExpanded}
              onToggle={handleToggle}
              colorClass="text-yellow-600 dark:text-yellow-400"
              toggleRef={toggleButtonRef}
            />
          )}
          <p className="text-xs text-yellow-500 dark:text-yellow-400 mt-2 flex items-center gap-1">
            <Clock className="w-3 h-3" />
            {formatElapsedTime(elapsed)} elapsed
          </p>
        </div>
      </div>
    )
  }

  // Auth required state
  if (status === "auth_required") {
    const authContent = statusMessage || "Please authenticate to continue."
    const isLong = authContent.length > LONG_CONTENT_THRESHOLD
    return (
      <div className="flex w-full animate-in fade-in slide-in-from-bottom-2 duration-300">
        <div className="flex-1 rounded-xl p-4 shadow-sm border border-orange-200 dark:border-orange-800 bg-gradient-to-br from-orange-50/50 to-amber-50/30 dark:from-orange-950/50 dark:to-amber-950/30 message-bubble text-orange-600 dark:text-orange-400">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <AgentLink
                agentId={agentId}
                agentName={agentName}
                avatarClassName="w-6 h-6 rounded-full flex items-center justify-center font-semibold border shrink-0 bg-gradient-to-br from-orange-100 to-amber-100 dark:from-orange-900 dark:to-amber-900 border-orange-300 dark:border-orange-700"
                avatarChildren={<KeyRound className="h-3 w-3 text-orange-600 dark:text-orange-300" />}
                nameClassName="text-orange-700 dark:text-orange-300"
              />
              <StepIndicator stepNumber={stepNumber} totalSteps={totalSteps} />
            </div>
            <span className="text-xs text-orange-600 dark:text-orange-400 font-medium">
              Authentication required
            </span>
          </div>
          <div className={`text-sm text-orange-700 dark:text-orange-300${!isExpanded && isLong ? ' line-clamp-4' : ''}`}>
            <MarkdownContent content={authContent} />
          </div>
          {isLong && (
            <CollapseToggle
              isExpanded={isExpanded}
              onToggle={handleToggle}
              colorClass="text-orange-600 dark:text-orange-400"
              toggleRef={toggleButtonRef}
            />
          )}
          <p className="text-xs text-orange-500 dark:text-orange-400 mt-2 flex items-center gap-1">
            <Clock className="w-3 h-3" />
            {formatElapsedTime(elapsed)} elapsed
          </p>
        </div>
      </div>
    )
  }

  // Working/Submitted states (default - in progress)
  // Prioritize dynamic status_message from the agent (A2A TaskStatus.message),
  // fall back to a friendly generic message.
  const primaryText = statusMessage || 'Working on your request...'
  
  return (
    <div className="flex w-full animate-in fade-in slide-in-from-bottom-2 duration-300">
      <div className="flex-1 rounded-xl p-4 shadow-sm border border-blue-200 dark:border-blue-800 bg-gradient-to-br from-blue-50/50 to-indigo-50/30 dark:from-blue-950/50 dark:to-indigo-950/30 message-bubble text-blue-600 dark:text-blue-300">
        {/* Header */}
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <AgentLink
              agentId={agentId}
              agentName={agentName}
              avatarClassName="w-6 h-6 rounded-full flex items-center justify-center font-semibold border shrink-0 bg-gradient-to-br from-blue-100 to-indigo-100 dark:from-blue-900 dark:to-indigo-900 border-blue-300 dark:border-blue-700"
              avatarChildren={<Sparkles className="h-3 w-3 text-blue-600 dark:text-blue-300 animate-pulse" />}
              nameClassName="text-blue-700 dark:text-blue-300"
            />
            <StepIndicator stepNumber={stepNumber} totalSteps={totalSteps} />
          </div>
          <span className="text-xs text-muted-foreground">
            Working...
          </span>
        </div>

        {/* Primary status */}
        <div className="space-y-2">
          <div className="flex items-start gap-2">
            <Loader2 className="w-4 h-4 animate-spin text-blue-600 dark:text-blue-400 mt-0.5 shrink-0" />
            <span className="text-sm text-blue-600 dark:text-blue-300">
              {primaryText}
            </span>
          </div>
          
          {/* Elapsed time */}
          <p className="text-xs text-blue-500 dark:text-blue-400 flex items-center gap-1">
            <Clock className="w-3 h-3" />
            {formatElapsedTime(elapsed)} elapsed
          </p>
        </div>
      </div>
    </div>
  )
}
