import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

let HitlPanel: any
beforeEach(async () => {
  const mod = await import('@/components/hitl-inline-reply-form')
  HitlPanel = mod.HitlPanel
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function makeHitlEntity(overrides: Record<string, unknown> = {}) {
  return {
    id: 'entity-1',
    roomId: 'room-1',
    messageType: 'agent' as const,
    content: 'Need clarification',
    senderName: 'Supervisor',
    timestamp: new Date().toISOString(),
    source: 'sse' as const,
    sourceVersion: 1,
    displayType: 'task-status' as const,
    isEphemeral: false,
    createdAt: Date.now(),
    updatedAt: Date.now(),
    hitlRequestId: 'req-1',
    hitlPrompt: 'What year range do you need?',
    hitlPromptType: 'text' as const,
    hitlResolved: false,
    ...overrides,
  }
}

const defaultPanelProps = {
  onSubmit: vi.fn().mockResolvedValue(undefined),
}

function renderPanel(entityOverrides: Record<string, unknown> = {}, propOverrides: Record<string, unknown> = {}) {
  const props = { ...defaultPanelProps, ...propOverrides }
  return render(
    <HitlPanel
      requests={[makeHitlEntity(entityOverrides)]}
      {...props}
    />
  )
}

/* ── HitlPanel: header, collapse, pagination ── */

describe('HitlPanel', () => {
  it('renders the "Question" header for single request', () => {
    renderPanel()
    expect(screen.getByText('Question')).toBeTruthy()
  })

  it('renders the question prompt', () => {
    renderPanel()
    expect(screen.getByText('What year range do you need?')).toBeTruthy()
  })

  it('can collapse and expand the panel', async () => {
    const user = userEvent.setup()
    renderPanel()

    expect(screen.getByText('What year range do you need?')).toBeTruthy()

    await user.click(screen.getByText('Question'))
    expect(screen.queryByText('What year range do you need?')).toBeNull()

    await user.click(screen.getByText('Question'))
    expect(screen.getByText('What year range do you need?')).toBeTruthy()
  })

  it('shows pagination controls for multiple requests', () => {
    const requests = [
      makeHitlEntity({ id: 'e1', hitlRequestId: 'req-1', hitlPrompt: 'Q1' }),
      makeHitlEntity({ id: 'e2', hitlRequestId: 'req-2', hitlPrompt: 'Q2' }),
    ]
    render(<HitlPanel requests={requests} {...defaultPanelProps} />)
    expect(screen.getByText('1/2')).toBeTruthy()
  })

  it('paginates between questions', async () => {
    const user = userEvent.setup()
    const requests = [
      makeHitlEntity({ id: 'e1', hitlRequestId: 'req-1', hitlPrompt: 'First question' }),
      makeHitlEntity({ id: 'e2', hitlRequestId: 'req-2', hitlPrompt: 'Second question' }),
    ]
    render(<HitlPanel requests={requests} {...defaultPanelProps} />)

    // Auto-advances to first unanswered question (index 0)
    expect(screen.getByText('First question')).toBeTruthy()

    await user.click(screen.getByLabelText('Next question'))

    await waitFor(() => {
      expect(screen.getByText('Second question')).toBeTruthy()
    })
  })

  it('returns null when requests array is empty', () => {
    const { container } = render(<HitlPanel requests={[]} {...defaultPanelProps} />)
    expect(container.innerHTML).toBe('')
  })
})

/* ── Text prompt type (design doc §5.7a Variant A) ── */

describe('HitlPanel text prompt type', () => {
  it('renders its own text input and Continue button', () => {
    renderPanel({ hitlPromptType: 'text' })
    expect(screen.getByPlaceholderText('Type your reply...')).toBeTruthy()
    expect(screen.getByRole('button', { name: /continue/i })).toBeTruthy()
  })

  it('has the input with correct aria-label', () => {
    renderPanel({ hitlPromptType: 'text', senderName: 'Research Agent' })
    expect(screen.getByLabelText('Reply to Research Agent')).toBeTruthy()
  })

  it('disables Continue when input is empty', () => {
    renderPanel({ hitlPromptType: 'text' })
    expect(screen.getByRole('button', { name: /continue/i })).toBeDisabled()
  })

  it('enables Continue when text is typed', async () => {
    const user = userEvent.setup()
    renderPanel({ hitlPromptType: 'text' })
    await user.type(screen.getByPlaceholderText('Type your reply...'), 'my answer')
    expect(screen.getByRole('button', { name: /continue/i })).not.toBeDisabled()
  })

  it('submits the typed text on Continue click', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    renderPanel(
      { hitlPromptType: 'text', hitlRequestId: 'req-42' },
      { onSubmit }
    )
    await user.type(screen.getByPlaceholderText('Type your reply...'), '2024-2026')
    await user.click(screen.getByRole('button', { name: /continue/i }))
    expect(onSubmit).toHaveBeenCalledWith('req-42', '2024-2026')
  })

  it('submits on Enter key', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    renderPanel(
      { hitlPromptType: 'text', hitlRequestId: 'req-42' },
      { onSubmit }
    )
    const input = screen.getByPlaceholderText('Type your reply...')
    await user.type(input, '2024-2026{enter}')
    expect(onSubmit).toHaveBeenCalledWith('req-42', '2024-2026')
  })

  it('shows "Reply sent" after successful submission', async () => {
    const user = userEvent.setup()
    renderPanel({ hitlPromptType: 'text' })
    await user.type(screen.getByPlaceholderText('Type your reply...'), 'done')
    await user.click(screen.getByRole('button', { name: /continue/i }))
    await waitFor(() => {
      expect(screen.getByText('Reply sent')).toBeTruthy()
    })
  })

  it('shows error message on submission failure', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn().mockRejectedValue(new Error('Network error'))
    renderPanel({ hitlPromptType: 'text' }, { onSubmit })
    await user.type(screen.getByPlaceholderText('Type your reply...'), 'test')
    await user.click(screen.getByRole('button', { name: /continue/i }))
    await waitFor(() => {
      expect(screen.getByText('Network error')).toBeTruthy()
    })
  })
})

/* ── Choice prompt type (design doc §5.7a Variant B) ── */

describe('HitlPanel choice prompt type', () => {
  const choices = ['Option A', 'Option B', 'Option C']

  it('renders lettered option buttons for each choice', () => {
    renderPanel({ hitlPromptType: 'choice', hitlChoices: choices })
    expect(screen.getByText('A')).toBeTruthy()
    expect(screen.getByText('B')).toBeTruthy()
    expect(screen.getByText('C')).toBeTruthy()
    expect(screen.getByText('Option A')).toBeTruthy()
    expect(screen.getByText('Option B')).toBeTruthy()
    expect(screen.getByText('Option C')).toBeTruthy()
  })

  it('renders a Submit button', () => {
    renderPanel({ hitlPromptType: 'choice', hitlChoices: choices })
    expect(screen.getByRole('button', { name: /submit/i })).toBeTruthy()
  })

  it('Submit button is disabled when no choice is selected', () => {
    renderPanel({ hitlPromptType: 'choice', hitlChoices: choices })
    expect(screen.getByRole('button', { name: /submit/i })).toBeDisabled()
  })

  it('renders an "Other" option with a text input', () => {
    renderPanel({ hitlPromptType: 'choice', hitlChoices: choices })
    expect(screen.getByText('Other:')).toBeTruthy()
    expect(screen.getByPlaceholderText('Type your own answer...')).toBeTruthy()
  })

  it('selects an option when clicked and enables Submit', async () => {
    const user = userEvent.setup()
    renderPanel({ hitlPromptType: 'choice', hitlChoices: choices })
    await user.click(screen.getByLabelText('Option A: Option A'))
    expect(screen.getByRole('button', { name: /submit/i })).not.toBeDisabled()
  })

  it('submits the selected choice on Submit click', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    renderPanel(
      { hitlPromptType: 'choice', hitlChoices: choices },
      { onSubmit }
    )
    await user.click(screen.getByLabelText('Option A: Option A'))
    await user.click(screen.getByRole('button', { name: /submit/i }))
    expect(onSubmit).toHaveBeenCalledWith('req-1', 'Option A')
  })

  it('submits custom text when "Other" is selected and typed', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    renderPanel(
      { hitlPromptType: 'choice', hitlChoices: choices },
      { onSubmit }
    )
    const otherInput = screen.getByPlaceholderText('Type your own answer...')
    await user.click(otherInput)
    await user.type(otherInput, 'My custom answer')
    await user.click(screen.getByRole('button', { name: /submit/i }))
    expect(onSubmit).toHaveBeenCalledWith('req-1', 'My custom answer')
  })

  it('disables Submit when "Other" is selected but input is empty', async () => {
    const user = userEvent.setup()
    renderPanel({ hitlPromptType: 'choice', hitlChoices: choices })
    await user.click(screen.getByLabelText('Other option'))
    expect(screen.getByRole('button', { name: /submit/i })).toBeDisabled()
  })

  it('shows "Reply sent" after successful submission', async () => {
    const user = userEvent.setup()
    renderPanel({ hitlPromptType: 'choice', hitlChoices: choices })
    await user.click(screen.getByLabelText('Option A: Option A'))
    await user.click(screen.getByRole('button', { name: /submit/i }))
    await waitFor(() => {
      expect(screen.getByText('Reply sent')).toBeTruthy()
    })
  })

  it('shows error message on submission failure', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn().mockRejectedValue(new Error('Network error'))
    renderPanel(
      { hitlPromptType: 'choice', hitlChoices: choices },
      { onSubmit }
    )
    await user.click(screen.getByLabelText('Option A: Option A'))
    await user.click(screen.getByRole('button', { name: /submit/i }))
    await waitFor(() => {
      expect(screen.getByText('Network error')).toBeTruthy()
    })
  })
})

/* ── Confirmation prompt type (design doc §5.7a Variant C) ── */

describe('HitlPanel confirmation prompt type', () => {
  it('renders Approve and Reject as lettered options', () => {
    renderPanel({ hitlPromptType: 'confirmation' })
    expect(screen.getByText('A')).toBeTruthy()
    expect(screen.getByText('B')).toBeTruthy()
    expect(screen.getByText('Approve')).toBeTruthy()
    expect(screen.getByText('Reject')).toBeTruthy()
  })

  it('calls onSubmit with "approved" when Approve is clicked', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    renderPanel({ hitlPromptType: 'confirmation' }, { onSubmit })
    await user.click(screen.getByText('Approve'))
    expect(onSubmit).toHaveBeenCalledWith('req-1', 'approved')
  })

  it('calls onSubmit with "rejected" when Reject is clicked', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    renderPanel({ hitlPromptType: 'confirmation' }, { onSubmit })
    await user.click(screen.getByText('Reject'))
    expect(onSubmit).toHaveBeenCalledWith('req-1', 'rejected')
  })

  it('shows "Reply sent" after successful Approve', async () => {
    const user = userEvent.setup()
    renderPanel({ hitlPromptType: 'confirmation' })
    await user.click(screen.getByText('Approve'))
    await waitFor(() => {
      expect(screen.getByText('Reply sent')).toBeTruthy()
    })
  })

  it('shows error on failure and keeps options', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn().mockRejectedValue(new Error('Server down'))
    renderPanel({ hitlPromptType: 'confirmation' }, { onSubmit })
    await user.click(screen.getByText('Approve'))
    await waitFor(() => {
      expect(screen.getByText('Server down')).toBeTruthy()
    })
    expect(screen.getByText('Approve')).toBeTruthy()
    expect(screen.getByText('Reject')).toBeTruthy()
  })

  it('does not render a text input or Submit button', () => {
    renderPanel({ hitlPromptType: 'confirmation' })
    expect(screen.queryByPlaceholderText('Type your reply...')).toBeNull()
    expect(screen.queryByRole('button', { name: /submit/i })).toBeNull()
  })
})

/* ── Multi-question group features ── */

describe('HitlPanel group-aware display', () => {
  it('shows "Questions (0/3 answered)" header for grouped requests', () => {
    const requests = [
      makeHitlEntity({ id: 'e1', hitlRequestId: 'req-1', hitlPrompt: 'Q1', hitlGroupId: 'g1', hitlGroupTotal: 3, hitlGroupIndex: 0 }),
      makeHitlEntity({ id: 'e2', hitlRequestId: 'req-2', hitlPrompt: 'Q2', hitlGroupId: 'g1', hitlGroupTotal: 3, hitlGroupIndex: 1 }),
      makeHitlEntity({ id: 'e3', hitlRequestId: 'req-3', hitlPrompt: 'Q3', hitlGroupId: 'g1', hitlGroupTotal: 3, hitlGroupIndex: 2 }),
    ]
    render(<HitlPanel requests={requests} {...defaultPanelProps} />)
    expect(screen.getByText('Questions (0/3 answered)')).toBeTruthy()
  })

  it('shows read-only Q&A display for answered questions', () => {
    const requests = [
      makeHitlEntity({ id: 'e1', hitlRequestId: 'req-1', hitlPrompt: 'Q1', hitlResolved: true, hitlUserAnswer: 'My answer' }),
      makeHitlEntity({ id: 'e2', hitlRequestId: 'req-2', hitlPrompt: 'Q2' }),
    ]
    render(<HitlPanel requests={requests} {...defaultPanelProps} />)
    // First page should auto-advance to Q2 (first unanswered)
    expect(screen.getByText('Q2')).toBeTruthy()
  })

  it('navigates to answered question and shows answer text', async () => {
    const user = userEvent.setup()
    const requests = [
      makeHitlEntity({ id: 'e1', hitlRequestId: 'req-1', hitlPrompt: 'Q1', hitlResolved: true, hitlUserAnswer: 'My answer' }),
      makeHitlEntity({ id: 'e2', hitlRequestId: 'req-2', hitlPrompt: 'Q2' }),
    ]
    render(<HitlPanel requests={requests} {...defaultPanelProps} />)
    // Navigate back to Q1 (answered)
    await user.click(screen.getByLabelText('Previous question'))
    await waitFor(() => {
      expect(screen.getByText('My answer')).toBeTruthy()
    })
  })

  it('shows "Question" for single non-grouped request', () => {
    renderPanel()
    expect(screen.getByText('Question')).toBeTruthy()
  })
})
