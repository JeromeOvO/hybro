'use client'

import { useState, useRef, useEffect, useCallback } from 'react'
import { Loader2, CheckCircle, XCircle, HelpCircle, ChevronUp, ChevronDown } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import type { HITLPromptType } from '@/lib/types/sse'
import type { MessageEntity } from '@/stores/message-store'
import { cn } from '@/lib/utils'

type FormState = 'idle' | 'submitting' | 'submitted' | 'error'

const OPTION_LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'

/* ─── Single question form (used inside the panel) ─── */

interface HitlQuestionFormProps {
  requestId: string
  prompt: string
  promptType: HITLPromptType
  choices?: string[] | null
  agentName: string
  onSubmit: (requestId: string, userInput: string) => Promise<void>
  backendError?: string | null
}

function HitlQuestionForm({
  requestId,
  prompt,
  promptType,
  choices,
  agentName,
  onSubmit,
  backendError,
}: HitlQuestionFormProps) {
  const [formState, setFormState] = useState<FormState>('idle')
  const [input, setInput] = useState('')
  const [selectedChoice, setSelectedChoice] = useState('')
  const [customChoice, setCustomChoice] = useState('')
  const [lastAction, setLastAction] = useState<'approved' | 'rejected' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const customChoiceRef = useRef<HTMLInputElement>(null)
  const submittingRef = useRef(false)

  const isSubmitting = formState === 'submitting'

  useEffect(() => {
    if (promptType === 'text') {
      inputRef.current?.focus()
    }
  }, [promptType])

  const handleSubmitValue = useCallback(async (value: string) => {
    if (!value.trim() || submittingRef.current) return
    submittingRef.current = true
    setFormState('submitting')
    setError(null)
    try {
      await onSubmit(requestId, value)
      setFormState('submitted')
    } catch (err) {
      submittingRef.current = false
      setFormState('error')
      setError(err instanceof Error ? err.message : 'Failed to send reply. Try again.')
    }
  }, [onSubmit, requestId])

  const CUSTOM_CHOICE_SENTINEL = '__hitl_other__'
  const isOtherSelected = selectedChoice === CUSTOM_CHOICE_SENTINEL
  const resolvedChoiceValue = isOtherSelected ? customChoice : selectedChoice

  const submitValue = promptType === 'text' ? input : resolvedChoiceValue
  const canSubmit = !isSubmitting && !!submitValue.trim()

  const handleConfirmation = useCallback((action: 'approved' | 'rejected') => {
    setLastAction(action)
    handleSubmitValue(action)
  }, [handleSubmitValue])

  if (formState === 'submitted') {
    return (
      <p className="text-sm text-emerald-600 dark:text-emerald-400 flex items-center gap-2 py-2">
        <CheckCircle className="h-4 w-4" />
        Reply sent
      </p>
    )
  }

  return (
    <div className="space-y-3">
      {/* Question text */}
      <p className="text-sm font-semibold text-foreground leading-relaxed">
        {prompt}
      </p>

      {/* Text prompt — own input + Continue button (design doc §5.7a Variant A) */}
      {promptType === 'text' && (
        <div className="flex gap-2 items-start">
          <Input
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Type your reply..."
            disabled={isSubmitting}
            aria-label={`Reply to ${agentName}`}
            data-testid="hitl-reply-input"
            className="flex-1 h-9 text-sm"
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                if (canSubmit) handleSubmitValue(input)
              }
            }}
          />
          <Button
            onClick={() => handleSubmitValue(input)}
            disabled={!canSubmit}
            size="sm"
            className="h-9 px-3 shrink-0"
          >
            {isSubmitting ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              'Continue'
            )}
          </Button>
        </div>
      )}

      {/* Choice prompt */}
      {promptType === 'choice' && choices?.length ? (
        <div className="space-y-1.5">
          {choices.map((choice, i) => {
            const letter = OPTION_LETTERS[i] || String(i + 1)
            const isSelected = selectedChoice === choice
            return (
              <button
                key={i}
                type="button"
                disabled={isSubmitting}
                onClick={() => setSelectedChoice(choice)}
                className={cn(
                  "w-full text-left px-3 py-2 rounded-lg flex items-start gap-3 transition-all duration-150 text-sm",
                  isSelected
                    ? "bg-primary/10 dark:bg-primary/15 ring-1 ring-primary/30"
                    : "hover:bg-muted/60 dark:hover:bg-muted/30",
                  isSubmitting && "opacity-60 cursor-not-allowed"
                )}
                aria-label={`Option ${letter}: ${choice}`}
              >
                <span className={cn(
                  "shrink-0 w-5 h-5 rounded text-[11px] font-bold flex items-center justify-center",
                  isSelected
                    ? "bg-primary text-primary-foreground"
                    : "bg-muted text-muted-foreground"
                )}>
                  {letter}
                </span>
                <span className={cn(
                  "leading-snug",
                  isSelected ? "text-foreground font-medium" : "text-foreground/80"
                )}>
                  {choice}
                </span>
              </button>
            )
          })}

          {/* Other option */}
          <div className={cn(
            "px-3 py-2 rounded-lg flex items-start gap-3 transition-all duration-150",
            isOtherSelected
              ? "bg-primary/10 dark:bg-primary/15 ring-1 ring-primary/30"
              : "hover:bg-muted/60 dark:hover:bg-muted/30"
          )}>
            <button
              type="button"
              disabled={isSubmitting}
              onClick={() => {
                setSelectedChoice(CUSTOM_CHOICE_SENTINEL)
                setTimeout(() => customChoiceRef.current?.focus(), 0)
              }}
              className={cn(
                "shrink-0 w-5 h-5 rounded text-[11px] font-bold flex items-center justify-center",
                isOtherSelected
                  ? "bg-primary text-primary-foreground"
                  : "bg-muted text-muted-foreground"
              )}
              aria-label="Other option"
            >
              {OPTION_LETTERS[choices.length] || '+'}
            </button>
            <div className="flex-1 min-w-0 flex items-center gap-2">
              <span className={cn(
                "text-sm shrink-0",
                isOtherSelected ? "text-foreground font-medium" : "text-foreground/80"
              )}>
                Other:
              </span>
              <Input
                ref={customChoiceRef}
                value={customChoice}
                onChange={(e) => setCustomChoice(e.target.value)}
                onFocus={() => setSelectedChoice(CUSTOM_CHOICE_SENTINEL)}
                placeholder="Type your own answer..."
                disabled={isSubmitting}
                className="flex-1 h-7 text-sm bg-transparent border-0 border-b border-border/40 rounded-none px-0 focus-visible:ring-0 focus-visible:border-primary/50"
              />
            </div>
          </div>
        </div>
      ) : null}

      {/* Confirmation prompt (design doc §5.7a Variant C) */}
      {promptType === 'confirmation' && (
        <div className="space-y-1.5">
          <button
            type="button"
            disabled={isSubmitting}
            onClick={() => handleConfirmation('approved')}
            className={cn(
              "w-full text-left px-3 py-2 rounded-lg flex items-center gap-3 transition-all duration-150 text-sm",
              lastAction === 'approved'
                ? "bg-emerald-500/10 ring-1 ring-emerald-500/30"
                : "hover:bg-muted/60 dark:hover:bg-muted/30",
              isSubmitting && "opacity-60 cursor-not-allowed"
            )}
          >
            <span className={cn(
              "shrink-0 w-5 h-5 rounded text-[11px] font-bold flex items-center justify-center",
              lastAction === 'approved' ? "bg-emerald-600 text-white" : "bg-muted text-muted-foreground"
            )}>A</span>
            <CheckCircle className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />
            <span>Approve</span>
            {isSubmitting && lastAction === 'approved' && (
              <Loader2 className="h-3.5 w-3.5 animate-spin ml-auto text-muted-foreground" />
            )}
          </button>
          <button
            type="button"
            disabled={isSubmitting}
            onClick={() => handleConfirmation('rejected')}
            className={cn(
              "w-full text-left px-3 py-2 rounded-lg flex items-center gap-3 transition-all duration-150 text-sm",
              lastAction === 'rejected'
                ? "bg-red-500/10 ring-1 ring-red-500/30"
                : "hover:bg-muted/60 dark:hover:bg-muted/30",
              isSubmitting && "opacity-60 cursor-not-allowed"
            )}
          >
            <span className={cn(
              "shrink-0 w-5 h-5 rounded text-[11px] font-bold flex items-center justify-center",
              lastAction === 'rejected' ? "bg-red-600 text-white" : "bg-muted text-muted-foreground"
            )}>B</span>
            <XCircle className="h-3.5 w-3.5 text-red-600 dark:text-red-400" />
            <span>Reject</span>
            {isSubmitting && lastAction === 'rejected' && (
              <Loader2 className="h-3.5 w-3.5 animate-spin ml-auto text-muted-foreground" />
            )}
          </button>
        </div>
      )}

      {/* Backend error (routing failure, etc.) — user can re-submit to retry */}
      {backendError && !error && (
        <p className="text-xs text-red-600 dark:text-red-400 flex items-center gap-1.5">
          <XCircle className="h-3.5 w-3.5 shrink-0" />
          {backendError} — you can retry your response.
        </p>
      )}

      {/* Local submit error */}
      {error && (
        <p className="text-xs text-red-600 dark:text-red-400 flex items-center gap-1.5">
          <XCircle className="h-3.5 w-3.5 shrink-0" />
          {error}
        </p>
      )}

      {/* Submit button for choice type only (text has inline Send, confirmation has inline buttons) */}
      {promptType === 'choice' && (
        <div className="flex items-center justify-end gap-2 pt-1">
          <Button
            onClick={() => handleSubmitValue(submitValue)}
            disabled={!canSubmit}
            size="sm"
            className="h-7 text-xs px-3"
          >
            {isSubmitting ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              'Submit'
            )}
          </Button>
        </div>
      )}
    </div>
  )
}

/* ─── Read-only Q&A display for already-answered questions ─── */

function HitlAnsweredDisplay({ prompt, answer }: { prompt: string; answer: string }) {
  return (
    <div className="space-y-1.5 py-2">
      <p className="text-sm font-semibold text-foreground leading-relaxed">{prompt}</p>
      <p className="text-sm text-muted-foreground bg-muted/40 rounded-md px-3 py-1.5">
        {answer}
      </p>
    </div>
  )
}

/* ─── Panel: HITL card attached above chat input ─── */

export interface HitlPanelProps {
  requests: MessageEntity[]
  onSubmit: (requestId: string, userInput: string) => Promise<void>
}

export function HitlPanel({ requests, onSubmit }: HitlPanelProps) {
  const [currentPage, setCurrentPage] = useState(0)
  const [isOpen, setIsOpen] = useState(true)

  const total = requests.length
  const answeredCount = requests.filter(r => r.hitlResolved || r.hitlUserAnswer).length
  const unansweredIndices = requests
    .map((r, i) => (!r.hitlResolved && !r.hitlUserAnswer) ? i : -1)
    .filter(i => i >= 0)

  const safeIndex = Math.min(currentPage, total - 1)
  const current = requests[safeIndex]

  // Auto-advance to first unanswered question on mount or when answer count changes
  useEffect(() => {
    if (unansweredIndices.length > 0) {
      setCurrentPage(unansweredIndices[0])
    }
  }, [answeredCount]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleSubmit = useCallback(async (requestId: string, userInput: string) => {
    await onSubmit(requestId, userInput)
    // After submission, auto-advance to next unanswered
    const nextUnanswered = requests.findIndex(
      (r, i) => i !== safeIndex && !r.hitlResolved && !r.hitlUserAnswer
    )
    if (nextUnanswered >= 0) {
      setCurrentPage(nextUnanswered)
    }
  }, [onSubmit, requests, safeIndex])

  if (!current || !current.hitlRequestId) return null

  const prompt = current.hitlPrompt || current.content || `${current.senderName} needs your input`
  const isAnswered = current.hitlResolved || !!current.hitlUserAnswer
  const isGrouped = current.hitlGroupTotal != null && current.hitlGroupTotal > 1

  return (
    <Collapsible open={isOpen} onOpenChange={setIsOpen}>
      <div className="border-b border-border/30 animate-in fade-in duration-200">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-1.5">
          <CollapsibleTrigger asChild>
            <button
              type="button"
              className="flex items-center gap-2 text-muted-foreground hover:text-foreground transition-colors"
            >
              <HelpCircle className="h-3.5 w-3.5" />
              <span className="text-xs font-medium text-foreground">
                {isGrouped ? `Questions (${answeredCount}/${total} answered)` : 'Question'}
              </span>
            </button>
          </CollapsibleTrigger>

          <div className="flex items-center gap-0.5">
            {/* Pagination */}
            {total > 1 && (
              <>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-5 w-5"
                  disabled={safeIndex === 0}
                  onClick={() => setCurrentPage(p => Math.max(0, p - 1))}
                  aria-label="Previous question"
                >
                  <ChevronUp className="h-3 w-3" />
                </Button>
                <span className="text-[10px] text-muted-foreground tabular-nums min-w-[3ch] text-center">
                  {safeIndex + 1}/{total}
                </span>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-5 w-5"
                  disabled={safeIndex >= total - 1}
                  onClick={() => setCurrentPage(p => Math.min(total - 1, p + 1))}
                  aria-label="Next question"
                >
                  <ChevronDown className="h-3 w-3" />
                </Button>
              </>
            )}

            {/* Collapse toggle */}
            <CollapsibleTrigger asChild>
              <Button variant="ghost" size="icon" className="h-5 w-5 text-muted-foreground">
                {isOpen ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
              </Button>
            </CollapsibleTrigger>
          </div>
        </div>

        {/* Body */}
        <CollapsibleContent>
          <div className="px-4 pb-3">
            {isAnswered ? (
              <HitlAnsweredDisplay
                prompt={prompt}
                answer={current.hitlUserAnswer || 'Answered'}
              />
            ) : (
              <HitlQuestionForm
                key={current.hitlRequestId}
                requestId={current.hitlRequestId}
                prompt={prompt}
                promptType={current.hitlPromptType || 'text'}
                choices={current.hitlChoices}
                agentName={current.senderName}
                onSubmit={handleSubmit}
                backendError={current.taskError}
              />
            )}
          </div>
        </CollapsibleContent>
      </div>
    </Collapsible>
  )
}
