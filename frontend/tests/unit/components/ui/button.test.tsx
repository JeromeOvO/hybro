import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { Button } from '@/components/ui/button'

afterEach(cleanup)

describe('Button variants', () => {
  it('renders danger as ghost with destructive text', () => {
    render(<Button variant="danger">Delete subnet</Button>)
    const button = screen.getByRole('button', { name: 'Delete subnet' })
    expect(button.className).toContain('text-destructive')
    expect(button.className).not.toMatch(/(^|\s)bg-destructive(\s|$)/)
  })

  it('renders dangerSolid with a destructive fill for confirmation dialogs', () => {
    render(<Button variant="dangerSolid">Delete</Button>)
    expect(screen.getByRole('button', { name: 'Delete' }).className).toMatch(/(^|\s)bg-destructive(\s|$)/)
  })

  it('renders secondary on the raised surface with a control border', () => {
    render(<Button variant="secondary">Add agent</Button>)
    const className = screen.getByRole('button', { name: 'Add agent' }).className
    expect(className).toContain('bg-surface-raised')
    expect(className).toContain('border-input')
  })

  it('offers a 32px icon size', () => {
    render(<Button size="iconSm" aria-label="Close">x</Button>)
    expect(screen.getByRole('button', { name: 'Close' }).className).toContain('size-8')
  })
})
