'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle,
  ArrowRight,
  CalendarDays,
  KeyRound,
  LoaderCircle,
  MessageCircleQuestion,
  RefreshCw,
  ShieldAlert,
  X,
} from 'lucide-react'
import { ApiError } from '@/lib/api-client'
import { useHitlInteractionController } from '@/hooks/useHitlInteractionController'
import type { HitlDraftValue } from '@/lib/hitl/interaction-controller'
import type { HITLPromptType } from '@/lib/types/sse'
import type { HitlLifecycleState } from '@/lib/selectors/conversation-types'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import {
  Questionnaire,
  QuestionnaireActions,
  QuestionnaireChoice,
  QuestionnaireChoices,
  QuestionnaireError,
  QuestionnaireInput,
  QuestionnaireItem,
  QuestionnaireNext,
  QuestionnairePrevious,
  QuestionnaireProgress,
  QuestionnaireSubmit,
  QuestionnaireTextarea,
  QuestionnaireTitle,
} from '@/components/ui/questionnaire'

export interface HitlPromptView {
  hitlId: string
  source: 'supervisor' | 'agent'
  agentName?: string
  prompt: string
  promptType: HITLPromptType
  choices?: string[]
  interactionId: string
  lifecycleState: HitlLifecycleState
  errorMessage?: string
  clientRequestId?: string
  answer?: string
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

type DraftValue = HitlDraftValue

const GENERIC_PROMPT = /^the agent needs additional information\.?$/i
const APPLYING_REFRESH_MS = 1500
const APPROVAL_CHOICES = ['Approve', 'Reject']
const CONFIRMATION_CHOICES = ['Confirm', 'Decline']
const AUTHENTICATION_CHOICES = [
  'Authentication complete',
  'Unable to authenticate',
]

function choiceOptions(hitl: HitlPromptView): string[] {
  if (
    hitl.promptType === 'choice'
    || hitl.promptType === 'single_choice'
    || hitl.promptType === 'multi_choice'
  ) {
    return hitl.choices ?? []
  }
  if (hitl.promptType === 'approval') return APPROVAL_CHOICES
  if (hitl.promptType === 'confirmation') return CONFIRMATION_CHOICES
  if (hitl.promptType === 'authentication') return AUTHENTICATION_CHOICES
  return []
}

function answerText(value: DraftValue | undefined): string {
  if (Array.isArray(value)) return value.join(', ')
  return value?.trim() ?? ''
}

function hasAnswer(_hitl: HitlPromptView, value: DraftValue | undefined): boolean {
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
  state: Exclude<HitlLifecycleState, 'open'>
  message?: string
  onCancel?: () => Promise<void>
  onRefresh?: () => Promise<void>
}) {
  const [working, setWorking] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const copy = {
    applying: {
      title: 'Applying your answers',
      body: 'Your responses are saved. Hybro is continuing the run.',
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
    setActionError(null)
    try {
      await action()
    } catch (error) {
      setActionError(
        error instanceof Error ? error.message : 'Unable to complete this request.',
      )
    } finally {
      setWorking(false)
    }
  }

  return (
    <Card role="status" aria-live="polite" className="gap-0 overflow-hidden py-0 shadow-lg">
      <CardContent className="flex flex-col gap-4 py-5 sm:flex-row sm:items-start">
        <Icon
          className={state === 'applying' ? 'size-5 shrink-0 animate-spin text-primary motion-reduce:animate-none' : 'size-5 shrink-0 text-primary'}
          aria-hidden="true"
        />
        <div className="min-w-0 flex-1">
          <h3 className="font-semibold">{copy.title}</h3>
          <p className="mt-1 max-w-[68ch] text-sm text-muted-foreground">
            {message || copy.body}
          </p>
          {actionError ? (
            <p className="mt-2 text-sm text-destructive" role="alert">
              {actionError}
            </p>
          ) : null}
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          {state === 'delivery_uncertain' && onRefresh ? (
            <Button variant="outline" disabled={working} onClick={() => run(onRefresh)}>
              <RefreshCw data-icon="inline-start" aria-hidden="true" />
              Check status
            </Button>
          ) : null}
          {(state === 'agent_timeout' || state === 'routing_failed') && onCancel ? (
            <Button variant="outline" disabled={working} onClick={() => run(onCancel)}>
              Cancel request
            </Button>
          ) : null}
        </div>
      </CardContent>
    </Card>
  )
}

function PromptQuestion({
  hitl,
  value,
  active,
  onChange,
}: {
  hitl: HitlPromptView
  value: DraftValue | undefined
  active: boolean
  onChange: (value: DraftValue) => void
}) {
  const selected = new Set(Array.isArray(value) ? value : value ? [value] : [])
  const textValue = Array.isArray(value) ? value.join(', ') : value ?? ''

  if (hitl.promptType === 'choice' || hitl.promptType === 'single_choice') {
    return (
      <QuestionnaireChoices>
        {(hitl.choices ?? []).map(choice => (
          <QuestionnaireChoice
            key={choice}
            value={choice}
            checked={selected.has(choice)}
            onChange={() => onChange(choice)}
          >
            <span className="font-medium">{choice}</span>
          </QuestionnaireChoice>
        ))}
      </QuestionnaireChoices>
    )
  }
  if (hitl.promptType === 'multi_choice') {
    return (
      <QuestionnaireChoices>
        {(hitl.choices ?? []).map(choice => {
          const checked = selected.has(choice)
          return (
            <QuestionnaireChoice
              key={choice}
              value={choice}
              checked={checked}
              onChange={() => {
                const next = new Set(selected)
                if (checked) next.delete(choice)
                else next.add(choice)
                onChange([...next])
              }}
            >
              <span className="font-medium">{choice}</span>
            </QuestionnaireChoice>
          )
        })}
      </QuestionnaireChoices>
    )
  }
  if (hitl.promptType === 'confirmation' || hitl.promptType === 'approval') {
    const choices = choiceOptions(hitl)
    return (
      <QuestionnaireChoices>
        {choices.map(choice => (
          <QuestionnaireChoice
            key={choice}
            value={choice}
            checked={selected.has(choice)}
            onChange={() => onChange(choice)}
          >
            <span className="font-medium">{choice}</span>
          </QuestionnaireChoice>
        ))}
      </QuestionnaireChoices>
    )
  }
  if (hitl.promptType === 'authentication') {
    return (
      <div className="grid gap-3 rounded-lg border bg-muted/40 p-3 sm:grid-cols-[auto_minmax(0,1fr)]">
        <KeyRound className="size-5 text-muted-foreground" aria-hidden="true" />
        <div className="min-w-0">
          <strong className="text-sm font-medium">Use the provider&apos;s secure sign-in page</strong>
          <p className="mt-1 text-sm text-muted-foreground">Never paste passwords, API keys, one-time codes, or recovery secrets into this chat.</p>
        </div>
        <QuestionnaireChoices className="sm:col-span-2">
          {choiceOptions(hitl).map(choice => (
            <QuestionnaireChoice
              key={choice}
              value={choice}
              checked={selected.has(choice)}
              onChange={() => onChange(choice)}
            >
              <span className="font-medium">{choice}</span>
            </QuestionnaireChoice>
          ))}
        </QuestionnaireChoices>
      </div>
    )
  }
  if (hitl.promptType === 'date') {
    return (
      <QuestionnaireInput
        type="date"
        value={textValue}
        onChange={event => onChange(event.target.value)}
      />
    )
  }
  if (hitl.promptType === 'textarea') {
    return (
      <QuestionnaireTextarea
        value={textValue}
        onChange={event => onChange(event.target.value)}
        placeholder={active ? 'Add the details needed to continue…' : undefined}
        rows={4}
      />
    )
  }
  return (
    <QuestionnaireInput
      value={textValue}
      onChange={event => onChange(event.target.value)}
      placeholder={active ? 'Type your answer…' : undefined}
    />
  )
}

export function HitlResponseBar({ hitls, onSubmit, onCancel, onRefresh }: HitlResponseBarProps) {
  const ordered = useMemo(() => sortHitls(hitls), [hitls])
  const interactionId = ordered[0]?.interactionId
  const interactionKey = `${interactionId ?? 'none'}:${ordered.map(hitl => hitl.hitlId).join(',')}`
  const seed = useMemo(() => ({
    interactionKey,
    firstRequestId: ordered[0]?.hitlId ?? null,
    answers: ordered.map(hitl => ({
      requestId: hitl.hitlId,
      answer: hitl.answer,
    })),
  }), [interactionKey, ordered])
  const { state: controller, dispatch } = useHitlInteractionController(seed)
  const {
    currentId,
    drafts,
    errorState,
    errorMessage: submissionError,
  } = controller
  const submitting = controller.submission === 'submitting'
  const submitted = controller.submission === 'submitted'
  const headingRef = useRef<HTMLHeadingElement>(null)
  const questionnaireRef = useRef<HTMLFormElement>(null)

  useEffect(() => {
    if (currentId && ordered.some(hitl => hitl.hitlId === currentId)) return
    const first = ordered[0]
    if (first) dispatch({ type: 'navigate', requestId: first.hitlId })
  }, [currentId, dispatch, ordered])

  const currentIndex = Math.max(0, ordered.findIndex(hitl => hitl.hitlId === currentId))
  const current = ordered[currentIndex] ?? ordered[0]
  const allAnswered = ordered.every(hitl => hasAnswer(hitl, drafts[hitl.hitlId]))

  // Authoritative lifecycle reconciliation: local submit/recovery state must
  // never override a fresher store lifecycle (e.g. SSE/refresh reporting
  // `failed`, `expired`, or `delivery_uncertain` after a successful submit).
  const authoritativeLifecycle = current?.lifecycleState
  const lastLifecycleRef = useRef<HitlLifecycleState | undefined>(undefined)
  useEffect(() => {
    if (!authoritativeLifecycle) return
    const previous = lastLifecycleRef.current
    lastLifecycleRef.current = authoritativeLifecycle
    if (previous !== undefined && previous !== authoritativeLifecycle) {
      dispatch({ type: 'server_reconciled', lifecycle: authoritativeLifecycle })
    }
  }, [authoritativeLifecycle, dispatch])

  const setDraft = useCallback((requestId: string, value: DraftValue) => {
    dispatch({ type: 'answer', requestId, value })
  }, [dispatch])

  const goTo = useCallback((index: number) => {
    const target = ordered[index]
    if (!target) return
    dispatch({ type: 'navigate', requestId: target.hitlId })
  }, [dispatch, ordered])

  const handleSubmit = useCallback(async () => {
    if (!interactionId || !allAnswered || submitting) return
    dispatch({ type: 'submit_started' })
    try {
      await onSubmit(
        interactionId,
        ordered.map(hitl => ({
          requestId: hitl.hitlId,
          answer: answerText(drafts[hitl.hitlId]),
        })),
        ordered.find(hitl => hitl.clientRequestId)?.clientRequestId,
      )
      dispatch({ type: 'submit_succeeded' })
    } catch (error) {
      const apiDetail = error instanceof ApiError && error.details && typeof error.details === 'object'
        ? (error.details as { detail?: { lifecycle_state?: string } }).detail
        : undefined
      if (
        error instanceof ApiError
        && (error.status === 503 || apiDetail?.lifecycle_state === 'delivery_uncertain')
      ) {
        dispatch({
          type: 'submit_failed',
          lifecycle: 'delivery_uncertain',
          message: 'The connection ended before receipt was confirmed.',
        })
      } else if (error instanceof ApiError && error.status === 410) {
        dispatch({
          type: 'submit_failed',
          lifecycle: 'expired',
          message: 'The response deadline passed before these answers were accepted.',
        })
      } else if (error instanceof ApiError && error.status === 409) {
        dispatch({
          type: 'submit_failed',
          message: 'This request changed before submission. Check its latest status before trying again.',
        })
      } else if (error instanceof ApiError && error.status === 502) {
        dispatch({
          type: 'submit_failed',
          lifecycle: 'routing_failed',
          message: 'Your answers were saved, but Hybro could not continue the run.',
        })
      } else if (error instanceof Error && error.name === 'AbortError') {
        dispatch({
          type: 'submit_failed',
          lifecycle: 'delivery_uncertain',
          message: 'The connection ended before receipt was confirmed.',
        })
      } else {
        dispatch({
          type: 'submit_failed',
          message: error instanceof Error ? error.message : 'Unable to submit these answers.',
        })
      }
    }
  }, [allAnswered, dispatch, drafts, interactionId, onSubmit, ordered, submitting])

  const lifecycleState = errorState ?? current?.lifecycleState
  const invalidPrompt = !current?.prompt.trim()
    || GENERIC_PROMPT.test(current?.prompt.trim() ?? '')
  const showApplying = Boolean(
    current && (submitted || lifecycleState === 'applying'),
  )
  const answerSurfaceReady = Boolean(
    current
    && !showApplying
    && lifecycleState === 'open'
    && !invalidPrompt,
  )
  const currentPromptType = current?.promptType

  useEffect(() => {
    if (!answerSurfaceReady) return
    const frame = window.requestAnimationFrame(() => {
      const acceptsTextInput = currentPromptType === 'text'
        || currentPromptType === 'textarea'
        || currentPromptType === 'date'
      const activeControl = acceptsTextInput
        ? questionnaireRef.current?.querySelector<HTMLElement>(
          '[data-slot="questionnaire-item"]:not([hidden]) input:not([disabled]), [data-slot="questionnaire-item"]:not([hidden]) textarea:not([disabled])',
        )
        : null
      const target = activeControl ?? headingRef.current
      target?.focus()
    })
    return () => window.cancelAnimationFrame(frame)
  }, [answerSurfaceReady, currentId, currentPromptType, submissionError, interactionKey])

  useEffect(() => {
    if (!showApplying || !onRefresh) return
    let cancelled = false
    const refresh = async () => {
      try {
        await onRefresh()
      } catch {
        // Transient refresh failures must not leave follow-up input hidden.
      }
    }
    void refresh()
    const timer = window.setInterval(() => {
      if (!cancelled) void refresh()
    }, APPLYING_REFRESH_MS)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [onRefresh, showApplying])

  if (!current) return null

  if (showApplying) {
    return (
      <section data-testid="hitl-response-bar" aria-label="Human input status">
        <RecoveryState state="applying" />
      </section>
    )
  }
  if (lifecycleState !== 'open' || invalidPrompt) {
    const state = invalidPrompt ? 'routing_failed' : lifecycleState
    return (
      <section data-testid="hitl-response-bar" aria-label="Human input status">
        <RecoveryState
          state={state as Exclude<HitlLifecycleState, 'open'>}
          message={submissionError ?? current.errorMessage}
          onRefresh={onRefresh}
          onCancel={onCancel ? () => onCancel(current.hitlId) : undefined}
        />
      </section>
    )
  }

  const sourceLabel = current.source === 'agent' ? (current.agentName ?? 'Agent') : 'HYBRO AI'
  const items = ordered.map(hitl => ({
    name: hitl.hitlId,
    required: true,
    choices: choiceOptions(hitl).map(value => ({ value })),
  }))
  const isLast = currentIndex === ordered.length - 1
  const currentAnswered = hasAnswer(current, drafts[current.hitlId])

  const handleFormSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (isLast) {
      if (allAnswered) void handleSubmit()
      return
    }
    if (currentAnswered) goTo(currentIndex + 1)
  }

  return (
    <section data-testid="hitl-response-bar" aria-labelledby="hitl-heading">
      <Questionnaire
        ref={questionnaireRef}
        items={items}
        item={currentId ?? undefined}
        onItemChange={requestId => {
          if (requestId && ordered.some(hitl => hitl.hitlId === requestId)) {
            dispatch({ type: 'navigate', requestId })
          }
        }}
        shortcuts="numbers"
        onSubmit={handleFormSubmit}
      >
        <Card className="gap-0 overflow-hidden py-0 shadow-lg">
          <CardHeader className="border-b py-4">
            <CardTitle className="flex items-center gap-2">
              <MessageCircleQuestion className="size-5 text-primary" aria-hidden="true" />
              <h2 id="hitl-heading" ref={headingRef} tabIndex={-1} className="outline-none">
                {sourceLabel} needs your input
              </h2>
            </CardTitle>
            {ordered.length > 1 ? (
              <CardDescription>
                <QuestionnaireProgress
                  render={(props, { current, total }) => (
                    <span {...props}>Question {current} of {total}</span>
                  )}
                />
              </CardDescription>
            ) : null}
          </CardHeader>

          <CardContent className="py-5">
            {ordered.map(hitl => (
              <QuestionnaireItem
                key={hitl.hitlId}
                name={hitl.hitlId}
                required
                multiple={hitl.promptType === 'multi_choice'}
              >
                <QuestionnaireTitle>{hitl.prompt}</QuestionnaireTitle>
                <PromptQuestion
                  hitl={hitl}
                  value={drafts[hitl.hitlId]}
                  active={hitl.hitlId === current.hitlId}
                  onChange={value => setDraft(hitl.hitlId, value)}
                />
                <QuestionnaireError />
              </QuestionnaireItem>
            ))}
            {submissionError ? (
              <p className="mt-3 text-sm text-destructive" role="alert">{submissionError}</p>
            ) : null}
          </CardContent>

          <CardFooter className="flex flex-col-reverse gap-2 border-t py-3 sm:flex-row sm:justify-between">
            <div>
              {onCancel ? (
                <Button variant="ghost" disabled={submitting} onClick={() => onCancel(current.hitlId)}>
                  Cancel request
                </Button>
              ) : null}
            </div>
            <QuestionnaireActions className="w-full sm:w-auto">
              {ordered.length > 1 ? (
                <QuestionnairePrevious disabled={submitting || currentIndex === 0}>
                  Back
                </QuestionnairePrevious>
              ) : null}
              {isLast ? (
                <QuestionnaireSubmit disabled={submitting || !allAnswered}>
                  {submitting ? (
                    <LoaderCircle
                      data-icon="inline-start"
                      className="animate-spin motion-reduce:animate-none"
                      aria-hidden="true"
                    />
                  ) : null}
                  {submitting ? 'Submitting…' : 'Submit'}
                </QuestionnaireSubmit>
              ) : (
                <QuestionnaireNext disabled={submitting || !currentAnswered}>
                  Next
                  <ArrowRight data-icon="inline-end" aria-hidden="true" />
                </QuestionnaireNext>
              )}
            </QuestionnaireActions>
          </CardFooter>
        </Card>
      </Questionnaire>
    </section>
  )
}
