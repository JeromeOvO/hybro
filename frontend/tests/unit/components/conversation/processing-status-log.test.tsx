import { afterEach, describe, expect, it } from 'vitest'
import userEvent from '@testing-library/user-event'
import { cleanup, fireEvent, render, screen, within } from '../../../utils/test-utils'
import { ProcessingStatusLog } from '@/components/conversation/ProcessingStatusLog'
import type { ProcessingStatusLogEntry } from '@/stores/message-store/types'

const entries: ProcessingStatusLogEntry[] = [
  {
    id: 'processing-log-1',
    message: 'Dispatching agents',
    timestamp: '2026-06-03T12:00:01.000Z',
  },
  {
    id: 'processing-log-2',
    message: 'Collecting results',
    timestamp: '2026-06-03T12:00:02.000Z',
  },
]

afterEach(() => {
  cleanup()
})

describe('ProcessingStatusLog', () => {
  it('renders the default Thinking update when there are no entries', () => {
    render(<ProcessingStatusLog entries={[]} />)

    expect(screen.getByRole('button', { name: /work logs/i })).toHaveTextContent('Work Logs')
    expect(within(screen.getByRole('log')).getByText('Thinking...')).toBeInTheDocument()
  })

  it('renders a compact two-row log region by default', () => {
    const { container } = render(<ProcessingStatusLog entries={entries} />)

    const button = screen.getByRole('button', { name: /work logs/i })
    expect(button).toHaveTextContent('Work Logs')
    expect(screen.queryByText('Processing updates')).not.toBeInTheDocument()
    expect(within(button).queryByText('Collecting results')).not.toBeInTheDocument()
    const log = screen.getByRole('log')
    expect(within(log).getByText('Dispatching agents')).toBeInTheDocument()
    expect(within(log).getByText('Collecting results')).toBeInTheDocument()
    expect(container.querySelector('time')).toBeNull()
    expect(container.querySelector('.conversation-processing-log-scroll')).toBeTruthy()
    expect(container.querySelector('.conversation-processing-log-scroll')).toHaveStyle({
      height: 'var(--conversation-processing-log-compact-height)',
    })
  })

  it('keeps the Work Logs header left aligned', () => {
    const { container } = render(<ProcessingStatusLog entries={entries} />)

    expect(container.querySelector('.conversation-processing-log-trigger')).toHaveStyle({
      justifyContent: 'flex-start',
    })
  })

  it('expands to a five-row log region when clicked', async () => {
    const { container } = render(<ProcessingStatusLog entries={entries} />)
    const trigger = screen.getByRole('button', { name: /work logs/i })

    expect(trigger).toHaveAttribute('aria-expanded', 'false')

    await userEvent.click(trigger)

    expect(trigger).toHaveAttribute('aria-expanded', 'true')
    expect(container.querySelector('.conversation-processing-log-scroll')).toHaveStyle({
      height: 'var(--conversation-processing-log-expanded-height)',
    })
  })

  it('marks only the latest running message for shimmer styling', () => {
    const { container } = render(<ProcessingStatusLog entries={entries} isRunning />)

    expect(container.querySelector('.conversation-processing-log')).toHaveClass('conversation-processing-log-running')
    expect(screen.getByText('Work Logs')).not.toHaveClass('conversation-processing-log-message-active')
    expect(screen.getByText('Dispatching agents')).not.toHaveClass('conversation-processing-log-message-active')
    expect(screen.getByText('Collecting results')).toHaveClass('conversation-processing-log-message-active')
  })

  it('does not mark completed logs as running', () => {
    const { container } = render(<ProcessingStatusLog entries={entries} isRunning={false} />)

    expect(container.querySelector('.conversation-processing-log')).not.toHaveClass('conversation-processing-log-running')
  })

  it('scrolls to the bottom when a new entry is appended while still near the bottom', () => {
    const { container, rerender } = render(<ProcessingStatusLog entries={entries.slice(0, 1)} />)
    const scroll = container.querySelector('.conversation-processing-log-scroll') as HTMLDivElement

    Object.defineProperties(scroll, {
      scrollHeight: { configurable: true, value: 500 },
      clientHeight: { configurable: true, value: 100 },
      scrollTop: { configurable: true, writable: true, value: 400 },
    })

    fireEvent.scroll(scroll)
    rerender(<ProcessingStatusLog entries={entries} />)

    expect(scroll.scrollTop).toBe(500)
  })

  it('does not force-scroll when a new entry is appended after the user scrolls up', () => {
    const { container, rerender } = render(<ProcessingStatusLog entries={entries.slice(0, 1)} />)
    const scroll = container.querySelector('.conversation-processing-log-scroll') as HTMLDivElement

    Object.defineProperties(scroll, {
      scrollHeight: { configurable: true, value: 500 },
      clientHeight: { configurable: true, value: 100 },
      scrollTop: { configurable: true, writable: true, value: 200 },
    })

    fireEvent.scroll(scroll)
    rerender(<ProcessingStatusLog entries={entries} />)

    expect(scroll.scrollTop).toBe(200)
  })
})
