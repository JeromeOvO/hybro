import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { UserAnswerCard } from '@/components/conversation/UserAnswerCard'

describe('UserAnswerCard', () => {
  it('renders the HITL answer in the lightweight conversation style', () => {
    render(
      <UserAnswerCard
        agentName="HITL Mock Agent"
        question="Human approval required."
        answer="approved"
      />,
    )

    const card = screen.getByTestId('hitl-answer-card')
    expect(card.className).toContain('conversation-hitl-answer')
    expect(screen.getByText('Response to HITL Mock Agent')).toBeTruthy()
    expect(screen.getByText('Human approval required.')).toBeTruthy()
    expect(screen.getByText('approved')).toBeTruthy()
  })
})
