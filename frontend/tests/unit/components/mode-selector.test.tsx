import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ModeSelector } from '@/components/mode-selector'
import type { ChatMode } from '@/lib/types/chat-mode'

const ALL_MODES: { mode: ChatMode; label: string; description: string }[] = [
  { mode: 'ultimate', label: 'Ultimate', description: 'For big tasks that need planning' },
  { mode: 'fast', label: 'Fast', description: 'For quick and simple questions' },
  { mode: 'ultimate_debate', label: 'Ultimate - Debate', description: 'For big tasks where different ideas should be compared' },
  { mode: 'fast_debate', label: 'Fast - Debate', description: 'For quick answers with extra checking' },
]

describe('ModeSelector', () => {
  afterEach(() => {
    cleanup()
  })

  // ── Renders correct label for every mode ──

  for (const { mode, label } of ALL_MODES) {
    it(`renders "${label}" label when mode is ${mode}`, () => {
      render(<ModeSelector mode={mode} onModeChange={vi.fn()} />)
      expect(screen.getByText(label)).toBeTruthy()
    })
  }

  // ── Trigger button tooltip shows current mode description ──

  for (const { mode, label, description } of ALL_MODES) {
    it(`trigger tooltip shows description for ${mode} on hover`, async () => {
      const user = userEvent.setup()
      render(<ModeSelector mode={mode} onModeChange={vi.fn()} />)

      await user.hover(screen.getByText(label))
      await waitFor(() => {
        expect(screen.getByRole('tooltip')).toBeTruthy()
      })
      expect(screen.getByRole('tooltip').textContent).toBe(description)
    })
  }

  // ── Switching between modes ──

  it('calls onModeChange with fast when clicking Fast item', async () => {
    const onModeChange = vi.fn()
    const user = userEvent.setup()
    render(<ModeSelector mode="ultimate" onModeChange={onModeChange} />)

    await user.click(screen.getByText('Ultimate'))
    await user.click(await screen.findByText('Fast'))

    expect(onModeChange).toHaveBeenCalledWith('fast')
  })

  it('calls onModeChange with ultimate when clicking Ultimate item', async () => {
    const onModeChange = vi.fn()
    const user = userEvent.setup()
    render(<ModeSelector mode="fast" onModeChange={onModeChange} />)

    await user.click(screen.getByText('Fast'))
    await user.click(await screen.findByText('Ultimate'))

    expect(onModeChange).toHaveBeenCalledWith('ultimate')
  })

  it('calls onModeChange with ultimate_debate when clicking Ultimate - Debate item', async () => {
    const onModeChange = vi.fn()
    const user = userEvent.setup()
    render(<ModeSelector mode="fast" onModeChange={onModeChange} />)

    await user.click(screen.getByText('Fast'))
    await user.click(await screen.findByText('Ultimate - Debate'))

    expect(onModeChange).toHaveBeenCalledWith('ultimate_debate')
  })

  it('calls onModeChange with fast_debate when clicking Fast - Debate item', async () => {
    const onModeChange = vi.fn()
    const user = userEvent.setup()
    render(<ModeSelector mode="ultimate" onModeChange={onModeChange} />)

    await user.click(screen.getByText('Ultimate'))
    await user.click(await screen.findByText('Fast - Debate'))

    expect(onModeChange).toHaveBeenCalledWith('fast_debate')
  })

  // ── Tooltip + click composition (high-risk path) ──
  // Radix DropdownMenuItem + TooltipTrigger asChild can swallow clicks.
  // These tests assert both: tooltip content appears AND click still fires.

  it('item tooltip appears on hover, then click still selects', async () => {
    const onModeChange = vi.fn()
    const user = userEvent.setup()
    render(<ModeSelector mode="ultimate" onModeChange={onModeChange} />)

    // Open dropdown
    await user.click(screen.getByText('Ultimate'))
    const fastDebateItem = await screen.findByText('Fast - Debate')

    // Hover → tooltip with description must appear
    await user.hover(fastDebateItem)
    await waitFor(() => {
      expect(screen.getByRole('tooltip')).toBeTruthy()
    })
    expect(screen.getByRole('tooltip').textContent).toBe(
      'For quick answers with extra checking'
    )

    // Click the same hovered item → onModeChange must fire
    await user.click(fastDebateItem)
    expect(onModeChange).toHaveBeenCalledWith('fast_debate')
  })

  it('item tooltip appears for Ultimate - Debate on hover, then click still selects', async () => {
    const onModeChange = vi.fn()
    const user = userEvent.setup()
    render(<ModeSelector mode="fast" onModeChange={onModeChange} />)

    await user.click(screen.getByText('Fast'))
    const ultimateDebateItem = await screen.findByText('Ultimate - Debate')

    await user.hover(ultimateDebateItem)
    await waitFor(() => {
      expect(screen.getByRole('tooltip')).toBeTruthy()
    })
    expect(screen.getByRole('tooltip').textContent).toBe(
      'For big tasks where different ideas should be compared'
    )

    await user.click(ultimateDebateItem)
    expect(onModeChange).toHaveBeenCalledWith('ultimate_debate')
  })

  // ── Disabled state ──

  it('does not open dropdown when disabled', async () => {
    const onModeChange = vi.fn()
    const user = userEvent.setup()
    render(<ModeSelector mode="ultimate" onModeChange={onModeChange} disabled />)

    await user.click(screen.getByText('Ultimate'))
    expect(screen.queryByText('Fast')).toBeNull()
    expect(screen.queryByText('Ultimate - Debate')).toBeNull()
  })

  // ── All 4 items visible in dropdown ──

  it('shows all 4 mode items in the dropdown', async () => {
    const user = userEvent.setup()
    render(<ModeSelector mode="ultimate" onModeChange={vi.fn()} />)

    await user.click(screen.getByText('Ultimate'))

    // The trigger already shows "Ultimate", so check the other 3 plus
    // verify there are at least 2 elements matching "Ultimate" (trigger + item)
    expect(screen.getAllByText('Ultimate').length).toBeGreaterThanOrEqual(2)
    expect(await screen.findByText('Fast')).toBeTruthy()
    expect(await screen.findByText('Ultimate - Debate')).toBeTruthy()
    expect(await screen.findByText('Fast - Debate')).toBeTruthy()
  })
})
