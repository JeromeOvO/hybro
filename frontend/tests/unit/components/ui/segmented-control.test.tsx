import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SegmentedControl } from '@/components/ui/segmented-control'

const options = [{ value: 'topology', label: 'Topology' }, { value: 'structure', label: 'Structure' }] as const

afterEach(cleanup)

describe('SegmentedControl', () => {
  it('marks the active option and changes on click', async () => {
    const onValueChange = vi.fn()
    render(<SegmentedControl label="View" value="topology" options={[...options]} onValueChange={onValueChange} />)
    expect(screen.getByRole('radio', { name: 'Topology' })).toHaveAttribute('aria-checked', 'true')
    await userEvent.click(screen.getByRole('radio', { name: 'Structure' }))
    expect(onValueChange).toHaveBeenCalledWith('structure')
  })

  it('moves selection with arrow keys and keeps one tab stop', async () => {
    const onValueChange = vi.fn()
    render(<SegmentedControl label="View" value="topology" options={[...options]} onValueChange={onValueChange} />)
    expect(screen.getByRole('radio', { name: 'Structure' })).toHaveAttribute('tabindex', '-1')
    screen.getByRole('radio', { name: 'Topology' }).focus()
    await userEvent.keyboard('{ArrowRight}')
    expect(onValueChange).toHaveBeenCalledWith('structure')
  })
})
