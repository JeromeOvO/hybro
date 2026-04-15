import React from 'react'
import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { InlineChips } from '@/components/inline-chips'

afterEach(() => cleanup())

describe('InlineChips', () => {
  it('renders step count when eventCount provided', () => {
    render(<InlineChips eventCount={4} />)
    expect(screen.getByText('4 steps')).toBeTruthy()
  })

  it('renders "1 step" for singular', () => {
    render(<InlineChips eventCount={1} />)
    expect(screen.getByText('1 step')).toBeTruthy()
  })

  it('renders duration when durationMs provided', () => {
    render(<InlineChips durationMs={3200} />)
    expect(screen.getByText('3.2s')).toBeTruthy()
  })

  it('renders both chips together', () => {
    render(<InlineChips eventCount={4} durationMs={3200} />)
    expect(screen.getByText('4 steps')).toBeTruthy()
    expect(screen.getByText('3.2s')).toBeTruthy()
  })

  it('renders nothing when no data', () => {
    const { container } = render(<InlineChips />)
    expect(container.firstElementChild?.children.length ?? 0).toBe(0)
  })

  it('has proper aria-label', () => {
    render(<InlineChips eventCount={4} durationMs={3200} />)
    expect(screen.getByLabelText('4 steps, 3.2 seconds')).toBeTruthy()
  })
})
