import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'

import { TruncatedContent } from '@/components/truncated-content'

function mockTruncation(truncated: boolean) {
  Object.defineProperty(HTMLElement.prototype, 'scrollHeight', {
    configurable: true,
    get() { return truncated ? 200 : 20 },
  })
  Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
    configurable: true,
    get() { return 20 },
  })
}

describe('TruncatedContent', () => {
  beforeEach(() => {
    mockTruncation(false)
  })

  afterEach(() => {
    cleanup()
  })

  it('renders short content without truncation controls', () => {
    render(<TruncatedContent content="Short text" />)
    expect(screen.getByText('Short text')).toBeTruthy()
    expect(screen.queryByTestId('truncated-fade')).toBeNull()
    expect(screen.queryByTestId('truncated-toggle')).toBeNull()
  })

  it('shows gradient fade and toggle for long content', () => {
    mockTruncation(true)
    render(<TruncatedContent content={'Line\n'.repeat(20)} maxLines={6} />)
    expect(screen.getByTestId('truncated-fade')).toBeTruthy()
    expect(screen.getByTestId('truncated-toggle')).toBeTruthy()
    expect(screen.getByText('Show more')).toBeTruthy()
  })

  it('expands content when "Show more" is clicked', () => {
    mockTruncation(true)
    render(<TruncatedContent content={'Line\n'.repeat(20)} maxLines={6} />)
    fireEvent.click(screen.getByText('Show more'))
    expect(screen.queryByTestId('truncated-fade')).toBeNull()
    expect(screen.getByText('Show less')).toBeTruthy()
  })

  it('collapses back when "Show less" is clicked', () => {
    mockTruncation(true)
    render(<TruncatedContent content={'Line\n'.repeat(20)} maxLines={6} />)
    fireEvent.click(screen.getByText('Show more'))
    expect(screen.getByText('Show less')).toBeTruthy()
    fireEvent.click(screen.getByText('Show less'))
    expect(screen.getByText('Show more')).toBeTruthy()
    expect(screen.getByTestId('truncated-fade')).toBeTruthy()
  })
})
