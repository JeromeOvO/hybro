import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ModeSelector } from '@/components/mode-selector'
import type { ChatMode } from '@/lib/types/chat-mode'

const SELECTABLE_MODES: { mode: ChatMode; label: string; description: string }[] = [
  { mode: 'ultimate', label: 'Ultimate', description: 'For big tasks that need planning' },
  { mode: 'fast', label: 'Fast', description: 'For quick and simple questions' },
]

describe('ModeSelector', () => {
  afterEach(() => {
    cleanup()
  })

  for (const { mode, label } of SELECTABLE_MODES) {
    it(`renders "${label}" label when mode is ${mode}`, () => {
      render(<ModeSelector mode={mode} onModeChange={vi.fn()} />)
      expect(screen.getByText(label)).toBeTruthy()
    })
  }

  for (const { mode, label, description } of SELECTABLE_MODES) {
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

  it('item tooltip appears on hover, then click still selects', async () => {
    const onModeChange = vi.fn()
    const user = userEvent.setup()
    render(<ModeSelector mode="ultimate" onModeChange={onModeChange} />)

    await user.click(screen.getByText('Ultimate'))
    const fastItem = await screen.findByText('Fast')

    await user.hover(fastItem)
    await waitFor(() => {
      expect(screen.getByRole('tooltip')).toBeTruthy()
    })
    expect(screen.getByRole('tooltip').textContent).toBe(
      'For quick and simple questions'
    )

    await user.click(fastItem)
    expect(onModeChange).toHaveBeenCalledWith('fast')
  })

  it('shows a disabled debate placeholder instead of selectable debate modes', async () => {
    const onModeChange = vi.fn()
    const user = userEvent.setup()
    render(<ModeSelector mode="ultimate" onModeChange={onModeChange} />)

    await user.click(screen.getByText('Ultimate'))

    const placeholder = await screen.findByText('Debate (Coming Soon)')
    const menuItem = placeholder.closest('[role="menuitem"]')
    expect(menuItem).not.toBeNull()
    expect(menuItem?.hasAttribute('data-disabled')).toBe(true)
    expect(screen.queryByText('Ultimate - Debate')).toBeNull()
    expect(screen.queryByText('Fast - Debate')).toBeNull()

    fireEvent.click(menuItem!)
    expect(onModeChange).not.toHaveBeenCalled()
  })

  it('does not open dropdown when disabled', async () => {
    const onModeChange = vi.fn()
    const user = userEvent.setup()
    render(<ModeSelector mode="ultimate" onModeChange={onModeChange} disabled />)

    await user.click(screen.getByText('Ultimate'))
    expect(screen.queryByText('Fast')).toBeNull()
    expect(screen.queryByText('Debate (Coming Soon)')).toBeNull()
  })

  it('shows Ultimate, Fast, and the debate placeholder in the dropdown', async () => {
    const user = userEvent.setup()
    render(<ModeSelector mode="ultimate" onModeChange={vi.fn()} />)

    await user.click(screen.getByText('Ultimate'))

    expect(screen.getAllByText('Ultimate').length).toBeGreaterThanOrEqual(2)
    expect(await screen.findByText('Fast')).toBeTruthy()
    expect(await screen.findByText('Debate (Coming Soon)')).toBeTruthy()
  })
})
