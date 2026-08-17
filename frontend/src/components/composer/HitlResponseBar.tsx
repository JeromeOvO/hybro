'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  CalendarDays,
  Check,
  CheckCircle2,
  FileUp,
  KeyRound,
  LoaderCircle,
  MessageCircleQuestion,
  RefreshCw,
  ShieldAlert,
  X,
} from 'lucide-react'
import { ApiError } from '@/lib/api-client'
import type { HITLPromptType } from '@/lib/types/sse'
import type { HitlLifecycleState } from '@/lib/selectors/conversation-types'
import { Button } from '@/components/ui/button'

export interface HitlPromptView {
  hitlId: string
  turnId: string
  ts: number
  source: 'supervisor' | 'agent'
  agentName?: string
  prompt: string
  promptType: HITLPromptType
  choices?: string[]
  interactionId: string
  lifecycleState: HitlLifecycleState
  errorMessage?: string
  expiresAt?: string
  clientRequestId?: string
  answer?: string
  groupId?: string
  groupTotal?: number
  groupIndex?: number
}

export interface HitlBatchAnswer {
  requestId: string
  answer: string
}

interface HitlResponseBarProps {
  hitls: HitlPromptView[]
  onSubmit: (
    interactionId: string,
    answers: HitlBatchAnswer[],
    clientRequestId?: string,
  ) => Promise<void>
  onCancel?: (requestId: string) => Promise<void>
  onRefresh?: () => Promise<void>
}

type DraftValue = string | string[]
type Drafts = Record<string, DraftValue>

const GENERIC_PROMPT = /^the agent needs additional information\.?$/i

function answerText(value: DraftValue | undefined): string {
  if (Array.isArray(value)) return value.join(', ')
  return value?.trim() ?? ''
}

function hasAnswer(hitl: HitlPromptView, value: DraftValue | undefined): boolean {
  if (hitl.promptType === 'file' || hitl.promptType === 'unknown') return false
  return answerText(value).length > 0
}

function sortHitls(hitls: HitlPromptView[]): HitlPromptView[] {
  return [...hitls].sort((a, b) => (
    (a.groupIndex ?? 0) - (b.groupIndex ?? 0)
    || a.hitlId.localeCompare(b.hitlId)
  ))
}

function RecoveryState({
  state,
  message,
  onCancel,
  onRefresh,
}: {
  state: Exclude<HitlLifecycleState, 'open' | 'submitting'>
  message?: string
  onCancel?: () => Promise<void>
  onRefresh?: () => Promise<void>
}) {
  const [working, setWorking] = useState(false)
  const copy = {
    applying: {
      title: 'Applying your answers',
      body: 'Your responses are saved. Hybro is waiting for the agent or supervisor to confirm the next step.',
      icon: LoaderCircle,
    },
    expired: {
      title: 'Input request expired',
      body: 'The response deadline passed, so this run can no longer continue from this request.',
      icon: CalendarDays,
    },
    agent_timeout: {
      title: 'The agent stopped responding',
      body: 'The agent did not acknowledge the task in time. No result was produced from this step.',
      icon: AlertTriangle,
    },
    delivery_uncertain: {
      title: 'Checking whether your answers were received',
      body: 'The connection ended before the agent confirmed receipt. Do not submit the answers again yet.',
      icon: RefreshCw,
    },
    routing_failed: {
      title: 'This input request cannot be answered',
      body: 'Hybro did not receive a valid question or could not route the response. Your run is paused.',
      icon: ShieldAlert,
    },
    canceled: {
      title: 'Input request canceled',
      body: 'This request is no longer actionable.',
      icon: X,
    },
  }[state]
  const Icon = copy.icon

  const run = async (action: (() => Promise<void>) | undefined) => {
    if (!action) return
    setWorking(true)
    try {
      await action()
    } finally {
      setWorking(false)
    }
  }

  return (
    <div className="conversation-hitl-recovery" role="status" aria-live="polite">
      <Icon className={`h-5 w-5 ${state === 'applying' ? 'animate-spin motion-reduce:animate-none' : ''}`} aria-hidden="true" />
      <div className="conversation-hitl-recovery-copy">
        <h3>{copy.title}</h3>
        <p>{message || copy.body}</p>
      </div>
      <div className="conversation-hitl-recovery-actions">
        {(state === 'applying' || state === 'delivery_uncertain') && onRefresh ? (
          <Button variant="outline" disabled={working} onClick={() => run(onRefresh)}>
            <RefreshCw className="h-4 w-4" aria-hidden="true" />
            Check status
          </Button>
        ) : null}
        {(state === 'agent_timeout' || state === 'routing_failed') && onCancel ? (
          <Button variant="outline" disabled={working} onClick={() => run(onCancel)}>
            Cancel request
          </Button>
        ) : null}
      </div>
    </div>
  )
}

function ChoiceControl({
  hitl,
  value,
  onChange,
  multiple = false,
}: {
  hitl: HitlPromptView
  value: DraftValue | undefined
  onChange: (value: DraftValue) => void
  multiple?: boolean
}) {
  const selected = new Set(Array.isArray(value) ? value : value ? [value] : [])
  return (
    <div
      className="conversation-hitl-options"
      role={multiple ? 'group' : 'radiogroup'}
      aria-label={hitl.prompt}
      data-testid="hitl-actions"
    >
      {(hitl.choices ?? []).map(choice => {
        const checked = selected.has(choice)
        return (
          <label
            key={choice}
            className="conversation-hitl-option-button"
            data-selected={checked ? 'true' : 'false'}
          >
            <input
              type={multiple ? 'checkbox' : 'radio'}
              name={`hitl-choice-${hitl.hitlId}`}
              value={choice}
              checked={checked}
              onChange={() => {
                if (!multiple) {
                  onChange(choice)
                  return
                }
                const next = new Set(selected)
                if (checked) next.delete(choice)
                else next.add(choice)
                onChange([...next])
              }}
              className="sr-only"
            />
            <span className="conversation-hitl-option-mark" aria-hidden="true">
              {checked ? <Check className="h-3.5 w-3.5" /> : null}
            </span>
            <span>{choice}</span>
          </label>
        )
      })}
    </div>
  )
}

function PromptControl({
  hitl,
  value,
  onChange,
  inputRef,
}: {
  hitl: HitlPromptView
  value: DraftValue | undefined
  onChange: (value: DraftValue) => void
  inputRef: React.RefObject<HTMLInputElement | HTMLTextAreaElement | null>
}) {
  const textValue = Array.isArray(value) ? value.join(', ') : value ?? ''

  if (hitl.promptType === 'choice' || hitl.promptType === 'single_choice') {
    return <ChoiceControl hitl={hitl} value={value} onChange={onChange} />
  }
  if (hitl.promptType === 'multi_choice') {
    return <ChoiceControl hitl={hitl} value={value} onChange={onChange} multiple />
  }
  if (hitl.promptType === 'confirmation' || hitl.promptType === 'approval') {
    const choices = hitl.promptType === 'approval'
      ? ['Approve', 'Reject']
      : ['Confirm', 'Decline']
    return <ChoiceControl hitl={{ ...hitl, choices }} value={value} onChange={onChange} />
  }
  if (hitl.promptType === 'authentication') {
    return (
      <div className="conversation-hitl-auth-control">
        <KeyRound className="h-5 w-5" aria-hidden="true" />
        <div>
          <strong>Use the provider&apos;s secure sign-in page</strong>
          <p>Never paste passwords, API keys, one-time codes, or recovery secrets into this chat.</p>
        </div>
        <ChoiceControl
          hitl={{ ...hitl, choices: ['Authentication complete', 'Unable to authenticate'] }}
          value={value}
          onChange={onChange}
        />
      </div>
    )
  }
  if (hitl.promptType === 'date') {
    return (
      <input
        ref={inputRef as React.RefObject<HTMLInputElement>}
        id={`hitl-answer-${hitl.hitlId}`}
        type="date"
        value={textValue}
        onChange={event => onChange(event.target.value)}
        className="conversation-hitl-text-input"
      />
    )
  }
  if (hitl.promptType === 'file') {
    return (
      <div className="conversation-hitl-unsupported" role="alert">
        <FileUp className="h-5 w-5" aria-hidden="true" />
        <div>
          <strong>File responses are not available for this request</strong>
          <p>Hybro will not pretend that a filename is an uploaded file. Cancel this request and restart the step with an attachment.</p>
        </div>
      </div>
    )
  }
  if (hitl.promptType === 'unknown') {
    return (
      <div className="conversation-hitl-unsupported" role="alert">
        <AlertTriangle className="h-5 w-5" aria-hidden="true" />
        <div>
          <strong>Unsupported input type</strong>
          <p>This request cannot be answered safely. Cancel it rather than sending an untyped response.</p>
        </div>
      </div>
    )
  }
  if (hitl.promptType === 'textarea') {
    return (
      <textarea
        ref={inputRef as React.RefObject<HTMLTextAreaElement>}
        id={`hitl-answer-${hitl.hitlId}`}
        value={textValue}
        onChange={event => onChange(event.target.value)}
        placeholder="Add the details needed to continue…"
        rows={4}
        className="conversation-hitl-text-input conversation-hitl-textarea"
      />
    )
  }
  return (
    <input
      ref={inputRef as React.RefObject<HTMLInputElement>}
      id={`hitl-answer-${hitl.hitlId}`}
      type="text"
      autoComplete="off"
      value={textValue}
      onChange={event => onChange(event.target.value)}
      placeholder="Type your answer…"
      className="conversation-hitl-text-input"
    />
  )
}

export function HitlResponseBar({ hitls, onSubmit, onCancel, onRefresh }: HitlResponseBarProps) {
  const ordered = useMemo(() => sortHitls(hitls), [hitls])
  const interactionId = ordered[0]?.interactionId
  const [currentId, setCurrentId] = useState<string | null>(ordered[0]?.hitlId ?? null)
  const [drafts, setDrafts] = useState<Drafts>(() => Object.fromEntries(
    ordered.filter(hitl => hitl.answer).map(hitl => [hitl.hitlId, hitl.answer as string]),
  ))
  const [reviewing, setReviewing] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const [submissionError, setSubmissionError] = useState<string | null>(null)
  const [errorState, setErrorState] = useState<HitlLifecycleState | null>(null)
  const headingRef = useRef<HTMLHeadingElement>(null)
  const inputRef = useRef<HTMLInputElement | HTMLTextAreaElement>(null)
  const previousInteractionRef = useRef(interactionId)

  useEffect(() => {
    if (previousInteractionRef.current !== interactionId) {
      previousInteractionRef.current = interactionId
      setDrafts(Object.fromEntries(
        ordered.filter(hitl => hitl.answer).map(hitl => [hitl.hitlId, hitl.answer as string]),
      ))
      setReviewing(false)
      setSubmitting(false)
      setSubmitted(false)
      setSubmissionError(null)
      setErrorState(null)
      setCurrentId(ordered[0]?.hitlId ?? null)
      return
    }
    if (!ordered.some(hitl => hitl.hitlId === currentId)) {
      setCurrentId(ordered[0]?.hitlId ?? null)
    }
  }, [currentId, interactionId, ordered])

  const currentIndex = Math.max(0, ordered.findIndex(hitl => hitl.hitlId === currentId))
  const current = ordered[currentIndex] ?? ordered[0]
  const allAnswered = ordered.every(hitl => hasAnswer(hitl, drafts[hitl.hitlId]))

  useEffect(() => {
    const target = reviewing ? headingRef.current : inputRef.current ?? headingRef.current
    target?.focus()
  }, [currentId, reviewing, submissionError])

  const setDraft = useCallback((requestId: string, value: DraftValue) => {
    setDrafts(previous => ({ ...previous, [requestId]: value }))
    setSubmissionError(null)
  }, [])

  const goTo = useCallback((index: number) => {
    const target = ordered[index]
    if (!target) return
    setCurrentId(target.hitlId)
    setReviewing(false)
  }, [ordered])

  const handleSubmit = useCallback(async () => {
    if (!interactionId || !allAnswered || submitting) return
    setSubmitting(true)
    setSubmissionError(null)
    try {
      await onSubmit(
        interactionId,
        ordered.map(hitl => ({
          requestId: hitl.hitlId,
          answer: answerText(drafts[hitl.hitlId]),
        })),
        ordered.find(hitl => hitl.clientRequestId)?.clientRequestId,
      )
      setSubmitted(true)
    } catch (error) {
      const apiDetail = error instanceof ApiError && error.details && typeof error.details === 'object'
        ? (error.details as { detail?: { lifecycle_state?: string } }).detail
        : undefined
      if (
        error instanceof ApiError
        && (error.status === 503 || apiDetail?.lifecycle_state === 'delivery_uncertain')
      ) {
        setErrorState('delivery_uncertain')
        setSubmissionError('The connection ended before receipt was confirmed.')
      } else if (error instanceof ApiError && error.status === 410) {
        setErrorState('expired')
        setSubmissionError('The response deadline passed before these answers were accepted.')
      } else if (error instanceof ApiError && error.status === 409) {
        setSubmissionError('This request changed before submission. Check its latest status before trying again.')
      } else if (error instanceof ApiError && error.status === 502) {
        setErrorState('routing_failed')
        setSubmissionError('Your answers were saved, but Hybro could not continue the run.')
      } else if (error instanceof Error && error.name === 'AbortError') {
        setErrorState('delivery_uncertain')
        setSubmissionError('The connection ended before receipt was confirmed.')
      } else {
        setSubmissionError(error instanceof Error ? error.message : 'Unable to submit these answers.')
      }
    } finally {
      setSubmitting(false)
    }
  }, [allAnswered, drafts, interactionId, onSubmit, ordered, submitting])

  if (!current) return null

  const lifecycleState = errorState ?? current.lifecycleState
  const invalidPrompt = !current.prompt.trim() || GENERIC_PROMPT.test(current.prompt.trim())
  if (submitted || lifecycleState === 'applying') {
    return (
      <section className="conversation-hitl-panel" data-testid="hitl-response-bar" aria-label="Human input status">
        <RecoveryState state="applying" onRefresh={onRefresh} />
      </section>
    )
  }
  if (lifecycleState !== 'open' || invalidPrompt) {
    const state = invalidPrompt ? 'routing_failed' : lifecycleState
    return (
      <section className="conversation-hitl-panel" data-testid="hitl-response-bar" aria-label="Human input status">
        <RecoveryState
          state={state as Exclude<HitlLifecycleState, 'open' | 'submitting'>}
          message={submissionError ?? current.errorMessage}
          onRefresh={onRefresh}
          onCancel={onCancel ? () => onCancel(current.hitlId) : undefined}
        />
      </section>
    )
  }

  const sourceLabel = current.source === 'agent' ? (current.agentName ?? 'Agent') : 'HYBRO AI'

  return (
    <section className="conversation-hitl-panel" data-testid="hitl-response-bar" aria-labelledby="hitl-heading">
      <header className="conversation-hitl-panel-header">
        <MessageCircleQuestion className="h-5 w-5 conversation-hitl-panel-icon" aria-hidden="true" />
        <div className="conversation-hitl-panel-heading-copy">
          <span className="conversation-hitl-panel-title">{sourceLabel} needs your input</span>
          <span className="conversation-hitl-panel-progress" aria-live="polite">
            {reviewing ? 'Review answers' : `Question ${currentIndex + 1} of ${ordered.length}`}
          </span>
        </div>
        <div className="conversation-hitl-progress-dots" aria-hidden="true">
          {ordered.map((hitl, index) => (
            <span
              key={hitl.hitlId}
              data-state={hasAnswer(hitl, drafts[hitl.hitlId]) ? 'answered' : index === currentIndex ? 'current' : 'pending'}
            />
          ))}
        </div>
      </header>

      {reviewing ? (
        <div className="conversation-hitl-review">
          <h2 id="hitl-heading" ref={headingRef} tabIndex={-1}>Review before sending</h2>
          <p>Hybro will send these answers together and resume the run once.</p>
          <ol className="conversation-hitl-review-list">
            {ordered.map((hitl, index) => (
              <li key={hitl.hitlId}>
                <div>
                  <span>{hitl.prompt}</span>
                  <strong>{answerText(drafts[hitl.hitlId]) || 'Not answered'}</strong>
                </div>
                <Button variant="ghost" size="sm" onClick={() => goTo(index)}>Edit</Button>
              </li>
            ))}
          </ol>
        </div>
      ) : (
        <div className="conversation-hitl-question" key={current.hitlId}>
          <h2 id="hitl-heading" ref={headingRef} tabIndex={-1}>{current.prompt}</h2>
          <label className="sr-only" htmlFor={`hitl-answer-${current.hitlId}`}>Answer</label>
          <PromptControl
            hitl={current}
            value={drafts[current.hitlId]}
            onChange={value => setDraft(current.hitlId, value)}
            inputRef={inputRef}
          />
        </div>
      )}

      {submissionError ? (
        <div className="conversation-hitl-error" role="alert">{submissionError}</div>
      ) : null}

      <footer className="conversation-hitl-footer">
        <div className="conversation-hitl-secondary-actions">
          {onCancel ? (
            <Button variant="ghost" disabled={submitting} onClick={() => onCancel(current.hitlId)}>
              Cancel request
            </Button>
          ) : null}
        </div>
        <div className="conversation-hitl-navigation">
          <Button
            variant="outline"
            disabled={submitting || (!reviewing && currentIndex === 0)}
            onClick={() => reviewing ? goTo(ordered.length - 1) : goTo(currentIndex - 1)}
          >
            <ArrowLeft className="h-4 w-4" aria-hidden="true" />
            Back
          </Button>
          {reviewing ? (
            <Button disabled={submitting || !allAnswered} onClick={handleSubmit}>
              {submitting ? <LoaderCircle className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden="true" /> : <CheckCircle2 className="h-4 w-4" aria-hidden="true" />}
              {submitting ? 'Submitting…' : 'Submit all answers'}
            </Button>
          ) : (
            <Button
              disabled={!hasAnswer(current, drafts[current.hitlId])}
              onClick={() => {
                if (currentIndex === ordered.length - 1) setReviewing(true)
                else goTo(currentIndex + 1)
              }}
            >
              {currentIndex === ordered.length - 1 ? 'Review answers' : 'Next'}
              <ArrowRight className="h-4 w-4" aria-hidden="true" />
            </Button>
          )}
        </div>
      </footer>
    </section>
  )
}
