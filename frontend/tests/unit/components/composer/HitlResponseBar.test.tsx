import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { HitlResponseBar, type HitlPromptView } from '@/components/composer/HitlResponseBar'

const baseHitl: HitlPromptView = {
  hitlId: 'hitl-1',
  interactionId: 'interaction-1',
  source: 'agent',
  agentName: 'HITL Mock Agent',
  prompt: 'Human approval required.',
  promptType: 'approval',
  lifecycleState: 'open',
}

function renderBar(hitls: HitlPromptView[], onSubmit = vi.fn().mockResolvedValue(undefined)) {
  return render(
    <HitlResponseBar
      hitls={hitls}
      onSubmit={onSubmit}
      onCancel={vi.fn().mockResolvedValue(undefined)}
      onRefresh={vi.fn().mockResolvedValue(undefined)}
    />,
  )
}

describe('HitlResponseBar', () => {
  afterEach(cleanup)

  it('uses one-question focus, preserves drafts by request id, and submits directly', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    renderBar([
      { ...baseHitl, hitlId: 'hitl-1', prompt: 'Company name?', promptType: 'text', groupIndex: 0 },
      { ...baseHitl, hitlId: 'hitl-2', prompt: 'Renewal date?', promptType: 'date', groupIndex: 1 },
    ], onSubmit)

    expect(screen.getByText('Question 1 of 2')).toBeDefined()
    fireEvent.change(screen.getByPlaceholderText('Type your answer…'), { target: { value: 'Acme' } })
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(screen.getByText('Renewal date?')).toBeDefined()

    fireEvent.change(document.querySelector('input[type="date"]') as HTMLInputElement, { target: { value: '2027-01-02' } })
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    expect((screen.getByPlaceholderText('Type your answer…') as HTMLInputElement).value).toBe('Acme')
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    fireEvent.click(screen.getByRole('button', { name: 'Submit' }))

    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith(
      'interaction-1',
      [
        { requestId: 'hitl-1', answer: 'Acme' },
        { requestId: 'hitl-2', answer: '2027-01-02' },
      ],
      undefined,
    ))
    expect(await screen.findByText('Applying your answers')).toBeDefined()
    expect(screen.queryByRole('button', { name: 'Check status' })).toBeNull()
  })

  it('submits a single question in one step without a review screen', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    renderBar([{ ...baseHitl, prompt: 'Where are you going?', promptType: 'text' }], onSubmit)

    expect(screen.queryByText('Question 1 of 1')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Review answers' })).toBeNull()
    fireEvent.change(screen.getByPlaceholderText('Type your answer…'), { target: { value: 'New York City' } })
    fireEvent.click(screen.getByRole('button', { name: 'Submit' }))

    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith(
      'interaction-1',
      [{ requestId: 'hitl-1', answer: 'New York City' }],
      undefined,
    ))
  })

  it('auto-refreshes while applying and autofocuses the follow-up open prompt', async () => {
    const onRefresh = vi.fn().mockResolvedValue(undefined)
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    const { rerender } = render(
      <HitlResponseBar
        hitls={[{ ...baseHitl, prompt: 'Where are you going?', promptType: 'text' }]}
        onSubmit={onSubmit}
        onRefresh={onRefresh}
      />,
    )

    fireEvent.change(screen.getByPlaceholderText('Type your answer…'), { target: { value: 'New York City' } })
    fireEvent.click(screen.getByRole('button', { name: 'Submit' }))
    await waitFor(() => expect(onSubmit).toHaveBeenCalled())
    expect(await screen.findByText('Applying your answers')).toBeDefined()
    await waitFor(() => expect(onRefresh).toHaveBeenCalled())

    rerender(
      <HitlResponseBar
        hitls={[{
          ...baseHitl,
          hitlId: 'hitl-2',
          interactionId: 'interaction-2',
          prompt: 'How many days/nights do you plan to spend in New York City?',
          promptType: 'text',
          lifecycleState: 'open',
        }]}
        onSubmit={onSubmit}
        onRefresh={onRefresh}
      />,
    )

    expect(await screen.findByText('How many days/nights do you plan to spend in New York City?')).toBeDefined()
    expect(screen.queryByText('Applying your answers')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Check status' })).toBeNull()
    await waitFor(() => {
      expect(document.activeElement).toBe(screen.getByPlaceholderText('Type your answer…'))
    })
  })

  it('autofocuses the next question control when advancing within an interaction', async () => {
    renderBar([
      { ...baseHitl, hitlId: 'hitl-1', prompt: 'Company name?', promptType: 'text', groupIndex: 0 },
      { ...baseHitl, hitlId: 'hitl-2', prompt: 'Budget?', promptType: 'text', groupIndex: 1 },
    ])

    fireEvent.change(screen.getByPlaceholderText('Type your answer…'), { target: { value: 'Acme' } })
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(screen.getByText('Budget?')).toBeDefined()
    await waitFor(() => {
      expect(document.activeElement).toBe(screen.getByPlaceholderText('Type your answer…'))
    })
  })

  it('renders accessible single and multi-choice controls', () => {
    const { rerender } = render(
      <HitlResponseBar
        hitls={[{ ...baseHitl, promptType: 'single_choice', choices: ['Use latest data', 'Use cached data'] }]}
        onSubmit={vi.fn()}
      />,
    )
    const latest = screen.getByRole('radio', { name: 'Use latest data' })
    fireEvent.click(latest)
    expect((latest as HTMLInputElement).checked).toBe(true)

    rerender(
      <HitlResponseBar
        hitls={[{ ...baseHitl, promptType: 'multi_choice', choices: ['Email', 'Phone'] }]}
        onSubmit={vi.fn()}
      />,
    )
    const email = screen.getByRole('checkbox', { name: 'Email' })
    fireEvent.click(email)
    expect((email as HTMLInputElement).checked).toBe(true)
  })

  it('registers textarea answers and submits directly', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    renderBar([{
      ...baseHitl,
      prompt: 'Add itinerary details',
      promptType: 'textarea',
    }], onSubmit)

    const textarea = screen.getByPlaceholderText('Add the details needed to continue…')
    fireEvent.change(textarea, { target: { value: 'Window seat and vegetarian meal' } })
    fireEvent.click(screen.getByRole('button', { name: 'Submit' }))
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith(
      'interaction-1',
      [{ requestId: 'hitl-1', answer: 'Window seat and vegetarian meal' }],
      undefined,
    ))
  })

  it('never asks for authentication secrets in free text', () => {
    renderBar([{ ...baseHitl, prompt: 'Sign in to the carrier', promptType: 'authentication' }])

    expect(screen.getByText(/Never paste passwords/)).toBeDefined()
    expect(screen.queryByRole('textbox')).toBeNull()
    expect(screen.getByRole('radio', { name: 'Authentication complete' })).toBeDefined()
  })

  it('renders generic prompts and delivery uncertainty as recovery states', () => {
    const { rerender } = renderBar([{
      ...baseHitl,
      prompt: 'The agent needs additional information.',
      promptType: 'text',
    }])
    expect(screen.getByText('This input request cannot be answered')).toBeDefined()
    expect(screen.queryByPlaceholderText('Type your answer…')).toBeNull()

    rerender(
      <HitlResponseBar
        hitls={[{ ...baseHitl, lifecycleState: 'delivery_uncertain' }]}
        onSubmit={vi.fn()}
        onRefresh={vi.fn().mockResolvedValue(undefined)}
      />,
    )
    expect(screen.getByText('Checking whether your answers were received')).toBeDefined()
    expect(screen.getByRole('button', { name: 'Check status' })).toBeDefined()
  })
})


describe('HitlResponseBar server lifecycle reconciliation', () => {
  afterEach(cleanup)

  it('leaves applying when the authoritative lifecycle reports failed', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    const { rerender } = render(
      <HitlResponseBar
        hitls={[{ ...baseHitl, promptType: 'text', lifecycleState: 'open' }]}
        onSubmit={onSubmit}
        onRefresh={vi.fn().mockResolvedValue(undefined)}
      />,
    )

    fireEvent.change(screen.getByPlaceholderText('Type your answer…'), { target: { value: 'Ok' } })
    fireEvent.click(screen.getByRole('button', { name: 'Submit' }))
    await waitFor(() => expect(onSubmit).toHaveBeenCalled())
    expect(await screen.findByText('Applying your answers')).toBeDefined()

    rerender(
      <HitlResponseBar
        hitls={[{ ...baseHitl, promptType: 'text', lifecycleState: 'expired' }]}
        onSubmit={onSubmit}
        onRefresh={vi.fn().mockResolvedValue(undefined)}
      />,
    )

    expect(await screen.findByText('Input request expired')).toBeDefined()
    expect(screen.queryByText('Applying your answers')).toBeNull()
  })

  it('clears stale local recovery state when the server returns to open', async () => {
    const { rerender } = render(
      <HitlResponseBar
        hitls={[{ ...baseHitl, promptType: 'text', lifecycleState: 'routing_failed' }]}
        onSubmit={vi.fn()}
      />,
    )
    expect(screen.getByText('This input request cannot be answered')).toBeDefined()

    rerender(
      <HitlResponseBar
        hitls={[{ ...baseHitl, promptType: 'text', lifecycleState: 'open' }]}
        onSubmit={vi.fn()}
      />,
    )

    expect(await screen.findByPlaceholderText('Type your answer…')).toBeDefined()
  })

  it('advances to the next question when Enter is pressed on an intermediate answer', () => {
    renderBar([
      { ...baseHitl, hitlId: 'hitl-1', prompt: 'Company name?', promptType: 'text', groupIndex: 0 },
      { ...baseHitl, hitlId: 'hitl-2', prompt: 'Renewal date?', promptType: 'date', groupIndex: 1 },
    ])

    const input = screen.getByPlaceholderText('Type your answer…')
    fireEvent.change(input, { target: { value: 'Acme' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(screen.getByText('Renewal date?')).toBeDefined()
  })

  it('submits from the last question when Enter is pressed', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    renderBar([{ ...baseHitl, prompt: 'Where are you going?', promptType: 'text' }], onSubmit)

    const input = screen.getByPlaceholderText('Type your answer…')
    fireEvent.change(input, { target: { value: 'Tokyo' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith(
      'interaction-1',
      [{ requestId: 'hitl-1', answer: 'Tokyo' }],
      undefined,
    ))
  })
})
