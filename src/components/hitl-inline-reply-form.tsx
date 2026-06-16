'use client'

import { useState, useRef, useEffect, useCallback } from 'react'
import { Loader2, CheckCircle, XCircle, ChevronUp, ChevronDown, ArrowUp } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
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
      <p className="text-xs text-muted-foreground flex items-center gap-1.5 py-1">
        <CheckCircle className="h-3.5 w-3.5 text-emerald-500" />
        Reply sent
      </p>
    )
  }

  return (
    <div className="space-y-2.5">
      {/* Prompt text */}
      <p className="text-[13px] text-foreground leading-relaxed whitespace-pre-wrap">
        {prompt}
      </p>

      {/* Text prompt — input + submit */}
      {promptType === 'text' && (
        <div className="flex gap-2 items-center">
          <Input
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={`Reply to ${agentName}...`}
            disabled={isSubmitting}
            aria-label={`Reply to ${agentName}`}
            data-testid="hitl-reply-input"
            className="flex-1 h-8 text-[13px] bg-transparent border-border/40 focus-visible:ring-1 focus-visible:ring-ring/30"
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                if (canSubmit) handleSubmitValue(input)
              }
            }}
          />
          <button
            type="button"
            onClick={() => handleSubmitValue(input)}
            disabled={!canSubmit}
            className={cn(
              "shrink-0 h-8 w-8 rounded-lg flex items-center justify-center transition-colors",
              canSubmit
                ? "bg-foreground text-background hover:bg-foreground/90"
                : "bg-muted text-muted-foreground cursor-not-allowed"
            )}
            aria-label="Send reply"
          >
            {isSubmitting ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <ArrowUp className="h-3.5 w-3.5" />
            )}
          </button>
        </div>
      )}

      {/* Choice prompt */}
      {promptType === 'choice' && choices?.length ? (
        <div className="space-y-1">
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
                  "w-full text-left px-3 py-1.5 rounded-lg flex items-start gap-2.5 transition-all duration-100 text-[13px]",
                  isSelected
                    ? "bg-foreground/[0.06] dark:bg-foreground/[0.08]"
                    : "hover:bg-foreground/[0.04] dark:hover:bg-foreground/[0.04]",
                  isSubmitting && "opacity-50 cursor-not-allowed"
                )}
                aria-label={`Option ${letter}: ${choice}`}
              >
                <span className={cn(
                  "shrink-0 w-5 h-5 rounded text-[10px] font-semibold flex items-center justify-center mt-px",
                  isSelected
                    ? "bg-foreground text-background"
                    : "bg-foreground/10 text-muted-foreground"
                )}>
                  {letter}
                </span>
                <span className={cn(
                  "leading-snug",
                  isSelected ? "text-foreground" : "text-foreground/80"
                )}>
                  {choice}
                </span>
              </button>
            )
          })}

          {/* Other option */}
          <div className={cn(
            "px-3 py-1.5 rounded-lg flex items-start gap-2.5 transition-all duration-100",
            isOtherSelected
              ? "bg-foreground/[0.06] dark:bg-foreground/[0.08]"
              : "hover:bg-foreground/[0.04] dark:hover:bg-foreground/[0.04]"
          )}>
            <button
              type="button"
              disabled={isSubmitting}
              onClick={() => {
                setSelectedChoice(CUSTOM_CHOICE_SENTINEL)
                setTimeout(() => customChoiceRef.current?.focus(), 0)
              }}
              className={cn(
                "shrink-0 w-5 h-5 rounded text-[10px] font-semibold flex items-center justify-center mt-px",
                isOtherSelected
                  ? "bg-foreground text-background"
                  : "bg-foreground/10 text-muted-foreground"
              )}
              aria-label="Other option"
            >
              {OPTION_LETTERS[choices.length] || '+'}
            </button>
            <div className="flex-1 min-w-0 flex items-center gap-2">
              <span className={cn(
                "text-[13px] shrink-0",
                isOtherSelected ? "text-foreground" : "text-foreground/80"
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
                className="flex-1 h-6 text-[13px] bg-transparent border-0 border-b border-border/30 rounded-none px-0 focus-visible:ring-0 focus-visible:border-foreground/30"
              />
            </div>
          </div>

          {/* Submit for choice */}
          <div className="flex justify-end pt-1">
            <button
              type="button"
              onClick={() => handleSubmitValue(submitValue)}
              disabled={!canSubmit}
              className={cn(
                "h-7 px-3 rounded-lg text-xs font-medium transition-colors",
                canSubmit
                  ? "bg-foreground text-background hover:bg-foreground/90"
                  : "bg-muted text-muted-foreground cursor-not-allowed"
              )}
            >
              {isSubmitting ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                'Submit'
              )}
            </button>
          </div>
        </div>
      ) : null}

      {/* Confirmation prompt */}
      {promptType === 'confirmation' && (
        <div className="flex items-center gap-2">
          <button
            type="button"
            disabled={isSubmitting}
            onClick={() => handleConfirmation('approved')}
            className={cn(
              "h-8 px-4 rounded-lg flex items-center gap-2 text-[13px] font-medium transition-colors",
              lastAction === 'approved'
                ? "bg-emerald-600 text-white dark:bg-emerald-500"
                : "bg-emerald-600 text-white hover:bg-emerald-700 dark:bg-emerald-500 dark:hover:bg-emerald-600",
              isSubmitting && "opacity-50 cursor-not-allowed"
            )}
          >
            <CheckCircle className="h-3.5 w-3.5" />
            Approve
            {isSubmitting && lastAction === 'approved' && (
              <Loader2 className="h-3 w-3 animate-spin" />
            )}
          </button>
          <button
            type="button"
            disabled={isSubmitting}
            onClick={() => handleConfirmation('rejected')}
            className={cn(
              "h-8 px-4 rounded-lg flex items-center gap-2 text-[13px] font-medium transition-colors",
              lastAction === 'rejected'
                ? "bg-red-600 text-white dark:bg-red-500"
                : "bg-red-600 text-white hover:bg-red-700 dark:bg-red-500 dark:hover:bg-red-600",
              isSubmitting && "opacity-50 cursor-not-allowed"
            )}
          >
            <XCircle className="h-3.5 w-3.5" />
            Reject
            {isSubmitting && lastAction === 'rejected' && (
              <Loader2 className="h-3 w-3 animate-spin" />
            )}
          </button>
        </div>
      )}

      {/* Error messages */}
      {backendError && !error && (
        <p className="text-xs text-red-500 dark:text-red-400 flex items-center gap-1.5">
          <XCircle className="h-3 w-3 shrink-0" />
          {backendError} — you can retry.
        </p>
      )}
      {error && (
        <p className="text-xs text-red-500 dark:text-red-400 flex items-center gap-1.5">
          <XCircle className="h-3 w-3 shrink-0" />
          {error}
        </p>
      )}
    </div>
  )
}

/* ─── Read-only Q&A display for already-answered questions ─── */

function HitlAnsweredDisplay({ prompt, answer }: { prompt: string; answer: string }) {
  return (
    <div className="space-y-1 py-1">
      <p className="text-[13px] text-foreground leading-relaxed">{prompt}</p>
      <p className="text-[13px] text-foreground/70">
        &rarr; {answer}
      </p>
    </div>
  )
}

/* ─── Panel: HITL block attached above chat input ─── */

// TODO(timeline): Show which conversation turn this HITL request belongs to
// (e.g. "Turn 3 — Agent X"). Requires page-level layout to pass turn context
// into this panel header.

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
    <div className="animate-in fade-in slide-in-from-bottom-2 duration-200">
      {/* Header row — agent label + pagination + collapse */}
      <div className="flex items-center justify-between px-4 pt-2.5 pb-1">
        <button
          type="button"
          onClick={() => setIsOpen(o => !o)}
          className="flex items-center gap-1.5 text-muted-foreground hover:text-foreground transition-colors"
        >
          <span className="text-xs font-medium text-foreground">
            {current.senderName}
          </span>
          <span className="text-[10px] text-muted-foreground">
            {isGrouped ? `${answeredCount}/${total}` : 'needs input'}
          </span>
          {!isOpen && (
            <ChevronDown className="h-3 w-3 text-muted-foreground/50" />
          )}
        </button>

        {isOpen && (
          <div className="flex items-center gap-0.5">
            {total > 1 && (
              <>
                <button
                  type="button"
                  className="h-5 w-5 flex items-center justify-center text-muted-foreground/60 hover:text-foreground transition-colors disabled:opacity-30"
                  disabled={safeIndex === 0}
                  onClick={() => setCurrentPage(p => Math.max(0, p - 1))}
                  aria-label="Previous question"
                >
                  <ChevronUp className="h-3 w-3" />
                </button>
                <span className="text-[10px] text-muted-foreground tabular-nums min-w-[3ch] text-center">
                  {safeIndex + 1}/{total}
                </span>
                <button
                  type="button"
                  className="h-5 w-5 flex items-center justify-center text-muted-foreground/60 hover:text-foreground transition-colors disabled:opacity-30"
                  disabled={safeIndex >= total - 1}
                  onClick={() => setCurrentPage(p => Math.min(total - 1, p + 1))}
                  aria-label="Next question"
                >
                  <ChevronDown className="h-3 w-3" />
                </button>
              </>
            )}
            <button
              type="button"
              onClick={() => setIsOpen(false)}
              className="h-5 w-5 flex items-center justify-center text-muted-foreground/40 hover:text-foreground transition-colors ml-0.5"
              aria-label="Collapse"
            >
              <ChevronUp className="h-3 w-3" />
            </button>
          </div>
        )}
      </div>

      {/* Body */}
      {isOpen && (
        <div className="px-4 pb-2.5">
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
      )}
    </div>
  )
}
