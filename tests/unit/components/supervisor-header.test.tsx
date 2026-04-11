import React from 'react'
import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { SupervisorHeader } from '@/components/supervisor-header'

afterEach(() => cleanup())

describe('SupervisorHeader', () => {
  it('renders HYBRO AI text', () => {
    render(<SupervisorHeader isCompleted={false} />)
    expect(screen.getByText('HYBRO AI')).toBeTruthy()
  })

  it('shows shimmer stage text when processing', () => {
    render(
      <SupervisorHeader
        isCompleted={false}
        stepNumber={2}
        totalSteps={3}
        details="Dispatching agents"
      />,
    )
    expect(screen.getByText('Step 2 of 3 · Dispatching agents')).toBeTruthy()
  })

  it('shows static stats when completed', () => {
    render(
      <SupervisorHeader
        isCompleted={true}
        agentCount={3}
        totalDurationMs={12400}
      />,
    )
    expect(screen.getByText('3 agents · 12.4s')).toBeTruthy()
  })

  it('has role status with aria-live', () => {
    render(<SupervisorHeader isCompleted={false} />)
    const header = screen.getByRole('status')
    expect(header.getAttribute('aria-live')).toBe('polite')
  })

  it('renders HYBRO favicon icon', () => {
    render(<SupervisorHeader isCompleted={false} />)
    const icon = screen.getByAltText('HYBRO AI')
    expect(icon.getAttribute('src')).toBe('/favicon.svg')
  })
})
