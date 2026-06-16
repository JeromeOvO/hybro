import { afterEach, describe, it, expect, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { HitlResponseBar, type HitlPromptView } from '@/components/composer/HitlResponseBar'

const baseHitl: HitlPromptView = {
  hitlId: 'hitl-1',
  turnId: 'turn-1',
  ts: 1,
  source: 'agent',
  agentName: 'HITL Mock Agent',
  prompt: 'Human approval required.',
  promptType: 'confirmation',
}

describe('HitlResponseBar', () => {
  afterEach(() => {
    cleanup()
  })

  it('renders confirmation actions as a vertical stack', () => {
    render(<HitlResponseBar hitls={[baseHitl]} onSubmit={vi.fn()} />)

    const actions = screen.getByTestId('hitl-actions')
    const approve = screen.getByRole('button', { name: 'Approve' })
    const reject = screen.getByRole('button', { name: 'Reject' })

    expect(actions.className).toContain('conversation-hitl-actions')
    expect(approve.className).toContain('conversation-hitl-option-button')
    expect(reject.className).toContain('conversation-hitl-option-button')
  })

  it('renders choice actions with the same vertical option button style', () => {
    render(
      <HitlResponseBar
        hitls={[{ ...baseHitl, promptType: 'choice', choices: ['Use latest data', 'Use cached data'] }]}
        onSubmit={vi.fn()}
      />,
    )

    expect(screen.getByTestId('hitl-actions').className).toContain('conversation-hitl-actions')
    expect(screen.getByRole('button', { name: 'Use latest data' }).className).toContain('conversation-hitl-option-button')
    expect(screen.getByRole('button', { name: 'Use cached data' }).className).toContain('conversation-hitl-option-button')
  })
})
