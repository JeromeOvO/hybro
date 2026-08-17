import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { UserAnswerCard } from '@/components/conversation/UserAnswerCard'

describe('UserAnswerCard', () => {
  afterEach(cleanup)

  it('renders a compact non-actionable resolved answer summary', () => {
    render(
      <UserAnswerCard
        agentName="Cyber Broker"
        question="Which market?"
        answer="Lloyd's"
      />,
    )

    expect(screen.getByTestId('hitl-answer-card')).toBeDefined()
    expect(screen.getByText("Lloyd's")).toBeDefined()
    expect(screen.queryByRole('button')).toBeNull()
    expect(screen.queryByRole('textbox')).toBeNull()
  })
})
