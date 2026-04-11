import React from 'react'
import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { HitlCompactCard } from '@/components/hitl-compact-card'
import { HitlQuestionCard } from '@/components/hitl-question-card'

afterEach(() => cleanup())

describe('HitlCompactCard', () => {
  it('renders truncated question and emphasized answer', () => {
    render(<HitlCompactCard prompt="What date range would you like?" answer="last 30 days" />)
    expect(screen.getByText('What date range would you like?')).toBeTruthy()
    expect(screen.getByText('last 30 days')).toBeTruthy()
  })

  it('has role status', () => {
    render(<HitlCompactCard prompt="Question?" answer="Answer" />)
    expect(screen.getByRole('status')).toBeTruthy()
  })

  it('shows green dot before answer', () => {
    const { container } = render(<HitlCompactCard prompt="Q?" answer="A" />)
    expect(container.querySelector('.bg-green-500')).toBeTruthy()
  })
})

describe('HitlQuestionCard', () => {
  it('renders question text', () => {
    render(<HitlQuestionCard prompt="What date range would you like?" />)
    expect(screen.getByText('What date range would you like?')).toBeTruthy()
  })

  it('shows Needs input shimmer label', () => {
    render(<HitlQuestionCard prompt="Question?" />)
    expect(screen.getByText('Needs input')).toBeTruthy()
  })

  it('has yellow-tinted border', () => {
    const { container } = render(<HitlQuestionCard prompt="Q?" />)
    const card = container.firstElementChild!
    expect(card.className).toContain('border-yellow-500/20')
  })

  it('has role status with aria-label', () => {
    render(<HitlQuestionCard prompt="Question?" />)
    expect(screen.getByRole('status')).toBeTruthy()
  })
})
