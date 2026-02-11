'use client'

import { useState, useEffect, useCallback, useRef } from 'react'
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
import { type TaskState, isTerminalState, isFailureState, PROCESSING_STATUS } from '@/lib/types/sse'
import { elapsedSeconds, formatElapsedTime } from '@/lib/time'
import { MarkdownContent } from './markdown-content'

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
  if (status === PROCESSING_STATUS.COMPLETED && content) {
    const isLong = content.length > LONG_CONTENT_THRESHOLD
    return (
      <div className="flex w-full animate-in fade-in slide-in-from-bottom-2 duration-300">
        <div className="flex-1 min-w-0 rounded-xl p-4 shadow-sm border border-emerald-200 dark:border-emerald-500/20 border-l-4 border-l-emerald-400 dark:border-l-emerald-500 bg-emerald-50 dark:bg-emerald-500/12 message-bubble text-emerald-600 dark:text-emerald-400">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <AgentLink
                agentId={agentId}
                agentName={agentName}
                avatarClassName="w-6 h-6 rounded-full flex items-center justify-center font-semibold border shrink-0 bg-emerald-100 dark:bg-emerald-500/15 border-emerald-300 dark:border-emerald-500/30"
                avatarChildren={<CheckCircle className="h-3 w-3 text-emerald-600 dark:text-emerald-400" />}
                nameClassName="text-emerald-700 dark:text-emerald-400"
              />
              <StepIndicator stepNumber={stepNumber} totalSteps={totalSteps} />
            </div>
            <span className="text-xs text-muted-foreground flex items-center gap-1">
              <CheckCircle className="w-3 h-3" />
              Completed in {formatElapsedTime(elapsed)}
            </span>
          </div>
          <div className={`text-emerald-800 dark:text-emerald-200${!isExpanded && isLong ? ' line-clamp-4' : ''}`}>
            <MarkdownContent content={content} />
          </div>
          {isLong && (
            <CollapseToggle
              isExpanded={isExpanded}
              onToggle={handleToggle}
              colorClass="text-emerald-600 dark:text-emerald-400"
              toggleRef={toggleButtonRef}
            />
          )}
        </div>
      </div>
    )
  }

  // Failed/Rejected/Canceled states
  if (isFailureState(status)) {
    const titles: Record<string, string> = {
      failed: "Task failed",
      rejected: "Task was rejected",
      canceled: "Task was canceled",
    }
    // Prefer error message; fall back to content (backend sometimes puts error info there)
    const displayBody = error || content || titles[status]
    const isLong = displayBody.length > LONG_CONTENT_THRESHOLD
    
    return (
      <div className="flex w-full animate-in fade-in slide-in-from-bottom-2 duration-300">
        <div className="flex-1 min-w-0 rounded-xl p-4 shadow-sm border border-red-200 dark:border-red-500/20 border-l-4 border-l-red-400 dark:border-l-red-500 bg-red-50 dark:bg-red-500/12 message-bubble text-red-600 dark:text-red-400">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <AgentLink
                agentId={agentId}
                agentName={agentName}
                avatarClassName="w-6 h-6 rounded-full flex items-center justify-center font-semibold border shrink-0 bg-red-100 dark:bg-red-500/15 border-red-300 dark:border-red-500/30"
                avatarChildren={<XCircle className="h-3 w-3 text-red-600 dark:text-red-400" />}
                nameClassName="text-red-700 dark:text-red-400"
              />
              <StepIndicator stepNumber={stepNumber} totalSteps={totalSteps} />
            </div>
            <span className="text-xs text-red-600 dark:text-red-400">
              {titles[status]}
            </span>
          </div>
          <div className={`text-sm text-red-700 dark:text-red-300${!isExpanded && isLong ? ' line-clamp-4' : ''}`}>
            <MarkdownContent content={displayBody} />
          </div>
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
        <div className="flex-1 min-w-0 rounded-xl p-4 shadow-sm border border-amber-200 dark:border-amber-500/20 border-l-4 border-l-amber-400 dark:border-l-amber-500 bg-amber-50 dark:bg-amber-500/12 message-bubble text-amber-700 dark:text-amber-400">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <AgentLink
                agentId={agentId}
                agentName={agentName}
                avatarClassName="w-6 h-6 rounded-full flex items-center justify-center font-semibold border shrink-0 bg-amber-100 dark:bg-amber-500/15 border-amber-300 dark:border-amber-500/30"
                avatarChildren={<AlertTriangle className="h-3 w-3 text-amber-600 dark:text-amber-400" />}
                nameClassName="text-amber-700 dark:text-amber-400"
              />
              <StepIndicator stepNumber={stepNumber} totalSteps={totalSteps} />
            </div>
            <span className="text-xs text-amber-700 dark:text-amber-400 font-medium">
              Input required
            </span>
          </div>
          <div className={`text-sm text-amber-700 dark:text-amber-300${!isExpanded && isLong ? ' line-clamp-4' : ''}`}>
            <MarkdownContent content={inputContent} />
          </div>
          {isLong && (
            <CollapseToggle
              isExpanded={isExpanded}
              onToggle={handleToggle}
              colorClass="text-amber-700 dark:text-amber-400"
              toggleRef={toggleButtonRef}
            />
          )}
          <p className="text-xs text-amber-600 dark:text-amber-400 mt-2 flex items-center gap-1">
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
        <div className="flex-1 min-w-0 rounded-xl p-4 shadow-sm border border-amber-200 dark:border-amber-500/20 border-l-4 border-l-amber-400 dark:border-l-amber-500 bg-amber-50 dark:bg-amber-500/12 message-bubble text-amber-700 dark:text-amber-400">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <AgentLink
                agentId={agentId}
                agentName={agentName}
                avatarClassName="w-6 h-6 rounded-full flex items-center justify-center font-semibold border shrink-0 bg-amber-100 dark:bg-amber-500/15 border-amber-300 dark:border-amber-500/30"
                avatarChildren={<KeyRound className="h-3 w-3 text-amber-600 dark:text-amber-400" />}
                nameClassName="text-amber-700 dark:text-amber-400"
              />
              <StepIndicator stepNumber={stepNumber} totalSteps={totalSteps} />
            </div>
            <span className="text-xs text-amber-700 dark:text-amber-400 font-medium">
              Authentication required
            </span>
          </div>
          <div className={`text-sm text-amber-700 dark:text-amber-300${!isExpanded && isLong ? ' line-clamp-4' : ''}`}>
            <MarkdownContent content={authContent} />
          </div>
          {isLong && (
            <CollapseToggle
              isExpanded={isExpanded}
              onToggle={handleToggle}
              colorClass="text-amber-700 dark:text-amber-400"
              toggleRef={toggleButtonRef}
            />
          )}
          <p className="text-xs text-amber-600 dark:text-amber-400 mt-2 flex items-center gap-1">
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
  const primaryText = statusMessage || taskContent || 'Working on your request...'
  
  return (
    <div className="flex w-full animate-in fade-in slide-in-from-bottom-2 duration-300">
      <div className="flex-1 min-w-0 rounded-xl p-4 shadow-sm border border-blue-200 dark:border-blue-500/20 border-l-4 border-l-blue-400 dark:border-l-blue-500 bg-blue-50 dark:bg-blue-500/12 message-bubble text-blue-600 dark:text-blue-400">
        {/* Header */}
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <AgentLink
              agentId={agentId}
              agentName={agentName}
              avatarClassName="w-6 h-6 rounded-full flex items-center justify-center font-semibold border shrink-0 bg-blue-100 dark:bg-blue-500/15 border-blue-300 dark:border-blue-500/30"
              avatarChildren={<Sparkles className="h-3 w-3 text-blue-600 dark:text-blue-400 animate-pulse" />}
              nameClassName="text-blue-700 dark:text-blue-400"
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
            <span className="text-sm text-blue-600 dark:text-blue-400 shimmer-text">
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
