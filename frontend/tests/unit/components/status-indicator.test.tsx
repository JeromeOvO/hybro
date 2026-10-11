import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { StatusIndicator } from '@/components/status-indicator'
import { roomStatusView, agentStatusView } from '@/lib/status'

afterEach(cleanup)

describe('StatusIndicator', () => {
  it('renders a neutral label next to the dot', () => {
    render(<StatusIndicator view={agentStatusView('active')} />)
    const label = screen.getByText('Online')
    expect(label.className).not.toContain('text-status')
  })

  it('colors only the Needs input label', () => {
    render(<StatusIndicator view={roomStatusView('awaiting_input')!} />)
    expect(screen.getByText('Needs input').className).toContain('text-status-warning')
  })

  it('keeps an accessible label when hidden visually', () => {
    render(<StatusIndicator view={agentStatusView('inactive')} hideLabel />)
    expect(screen.getByText('Offline').className).toContain('sr-only')
  })
})
