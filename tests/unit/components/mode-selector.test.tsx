import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ModeSelector } from '@/components/mode-selector'

describe('ModeSelector', () => {
  afterEach(() => {
    cleanup()
  })

  it('renders Ultimate label and icon when mode is ultimate', () => {
    render(<ModeSelector mode="ultimate" onModeChange={vi.fn()} />)
    expect(screen.getByText('Ultimate')).toBeTruthy()
  })

  it('renders Fast label and icon when mode is fast', () => {
    render(<ModeSelector mode="fast" onModeChange={vi.fn()} />)
    expect(screen.getByText('Fast')).toBeTruthy()
  })

  it('calls onModeChange with fast when clicking Fast item', async () => {
    const onModeChange = vi.fn()
    const user = userEvent.setup()
    render(<ModeSelector mode="ultimate" onModeChange={onModeChange} />)

    // Open dropdown
    await user.click(screen.getByText('Ultimate'))
    // Click Fast item
    const fastItem = await screen.findByText('Fast')
    await user.click(fastItem)

    expect(onModeChange).toHaveBeenCalledWith('fast')
  })

  it('calls onModeChange with ultimate when clicking Ultimate item', async () => {
    const onModeChange = vi.fn()
    const user = userEvent.setup()
    render(<ModeSelector mode="fast" onModeChange={onModeChange} />)

    // Open dropdown
    await user.click(screen.getByText('Fast'))
    // Click Ultimate item
    const ultimateItem = await screen.findByText('Ultimate')
    await user.click(ultimateItem)

    expect(onModeChange).toHaveBeenCalledWith('ultimate')
  })

  it('does not open dropdown when disabled', async () => {
    const onModeChange = vi.fn()
    const user = userEvent.setup()
    render(<ModeSelector mode="ultimate" onModeChange={onModeChange} disabled />)

    await user.click(screen.getByText('Ultimate'))
    // Dropdown should not open, so Fast item should not appear
    expect(screen.queryByText('Fast')).toBeNull()
  })
})
