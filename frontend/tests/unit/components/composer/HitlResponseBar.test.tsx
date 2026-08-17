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

  it('uses one-question focus, preserves drafts by request id, and reviews a batch', async () => {
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
    fireEvent.click(screen.getByRole('button', { name: 'Review answers' }))

    expect(screen.getByText('Review before sending')).toBeDefined()
    expect(screen.getByText('Acme')).toBeDefined()
    expect(screen.getByText('2027-01-02')).toBeDefined()
    fireEvent.click(screen.getByRole('button', { name: 'Submit all answers' }))

    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith(
      'interaction-1',
      [
        { requestId: 'hitl-1', answer: 'Acme' },
        { requestId: 'hitl-2', answer: '2027-01-02' },
      ],
      undefined,
    ))
    expect(await screen.findByText('Applying your answers')).toBeDefined()
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

  it('shows file requests as unsupported instead of pretending a filename is uploaded', () => {
    renderBar([{ ...baseHitl, prompt: 'Upload the signed form', promptType: 'file' }])
    expect(screen.getByText('Unsupported input type')).toBeDefined()
    expect(screen.queryByRole('button', { name: 'Review answers' })).toBeDisabled()
  })
})
