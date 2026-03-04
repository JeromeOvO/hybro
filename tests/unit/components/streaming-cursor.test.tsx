import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { StreamingCursor } from '@/components/streaming-cursor'

describe('StreamingCursor', () => {
  it('should render the cursor element', () => {
    const { container } = render(<StreamingCursor />)
    const cursor = container.querySelector('span')
    expect(cursor).toBeTruthy()
  })

  it('should have blinking animation class', () => {
    const { container } = render(<StreamingCursor />)
    const cursor = container.querySelector('span')
    expect(cursor?.className).toContain('animate-pulse')
  })

  it('should be inline-block for proper text flow', () => {
    const { container } = render(<StreamingCursor />)
    const cursor = container.querySelector('span')
    expect(cursor?.className).toContain('inline-block')
  })

  it('should be hidden from screen readers', () => {
    const { container } = render(<StreamingCursor />)
    const cursor = container.querySelector('span')
    expect(cursor?.getAttribute('aria-hidden')).toBe('true')
  })

  it('should have correct dimensions', () => {
    const { container } = render(<StreamingCursor />)
    const cursor = container.querySelector('span')
    expect(cursor?.className).toContain('w-2')
    expect(cursor?.className).toContain('h-4')
  })
})
