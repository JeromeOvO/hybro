import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react'
import { TASK_STATE } from '@/lib/types/sse'

vi.mock('@/components/markdown-content', () => ({
  MarkdownContent: ({ content }: { content: string }) => (
    <div data-testid="markdown">{content}</div>
  ),
}))

vi.mock('@/lib/time', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/time')>()
  return {
    ...actual,
    elapsedSeconds: () => 42,
  }
})

import { TaskStatusMessage } from '@/components/task-status-message'

describe('TaskStatusMessage', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  it('renders spinner in working state', () => {
    const { container } = render(
      <TaskStatusMessage
        internalId="t-1"
        agentName="Test Agent"
        initialStatus={TASK_STATE.WORKING}
      />
    )

    const spinner = container.querySelector('.animate-spin')
    expect(spinner).toBeTruthy()
    expect(screen.getByText('Working...')).toBeTruthy()
  })

  it('renders green indicator in completed state', () => {
    const { container } = render(
      <TaskStatusMessage
        internalId="t-2"
        agentName="Completed Agent"
        initialStatus={TASK_STATE.COMPLETED}
        content="Task done successfully"
      />
    )

    const greenBorder = container.querySelector('[class*="border-emerald"]')
    expect(greenBorder).toBeTruthy()
    expect(screen.getByText('Completed Agent')).toBeTruthy()
    expect(screen.getByTestId('markdown').textContent).toBe('Task done successfully')
  })

  it('renders red indicator in failed state', () => {
    const { container } = render(
      <TaskStatusMessage
        internalId="t-3"
        agentName="Failed Agent"
        initialStatus={TASK_STATE.FAILED}
        error="Something went wrong"
      />
    )

    expect(screen.getByText('Task failed')).toBeTruthy()
    expect(screen.getByTestId('markdown').textContent).toBe('Something went wrong')
    const redBorder = container.querySelector('[class*="border-red"]')
    expect(redBorder).toBeTruthy()
  })

  it('renders amber indicator in input_required state', () => {
    const { container } = render(
      <TaskStatusMessage
        internalId="t-4"
        agentName="Input Agent"
        initialStatus={TASK_STATE.INPUT_REQUIRED}
        hitlPrompt="Please provide more info"
        hitlResolved={true}
      />
    )

    expect(screen.getByText('Input provided')).toBeTruthy()
    expect(screen.getByTestId('markdown').textContent).toBe('Please provide more info')
    const amberBorder = container.querySelector('[class*="border-amber"]')
    expect(amberBorder).toBeTruthy()
  })

  it('long content collapses with toggle', () => {
    const longContent = 'A'.repeat(600)
    render(
      <TaskStatusMessage
        internalId="t-5"
        agentName="Long Agent"
        initialStatus={TASK_STATE.COMPLETED}
        content={longContent}
      />
    )

    expect(screen.getByText('Show more')).toBeTruthy()

    fireEvent.click(screen.getByText('Show more'))
    expect(screen.getByText('Show less')).toBeTruthy()
  })

  it('elapsed time counter displays formatted time', () => {
    render(
      <TaskStatusMessage
        internalId="t-6"
        agentName="Timer Agent"
        initialStatus={TASK_STATE.WORKING}
        taskCreatedAt={new Date(Date.now() - 42000).toISOString()}
      />
    )

    expect(screen.getByText('42s elapsed')).toBeTruthy()

    act(() => {
      vi.advanceTimersByTime(3000)
    })

    expect(screen.getByText('45s elapsed')).toBeTruthy()
  })
})
