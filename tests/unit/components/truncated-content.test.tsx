import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'

vi.mock('@/components/markdown-content', () => ({
  MarkdownContent: ({ content, className }: { content: string; className?: string }) => (
    <div data-testid="markdown-content" className={className}>
      {content}
    </div>
  ),
}))

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

  it('renders content through MarkdownContent', () => {
    render(<TruncatedContent content="Hello **world**" />)
    const md = screen.getByTestId('markdown-content')
    expect(md).toBeTruthy()
    expect(md.textContent).toContain('Hello **world**')
  })

  it('does not use whitespace-pre-wrap on content wrapper', () => {
    render(<TruncatedContent content="test" />)
    const body = screen.getByTestId('truncated-content-body')
    expect(body.className).not.toContain('whitespace-pre-wrap')
  })

  it('uses inline max-height for truncation, not -webkit-line-clamp', () => {
    render(<TruncatedContent content="test" maxLines={6} />)
    const body = screen.getByTestId('truncated-content-body')
    expect(body.style.maxHeight).toBe('6lh')
    expect(body.className).toContain('overflow-hidden')
    expect(body.style.webkitLineClamp).toBeFalsy()
    expect(body.style.display).not.toBe('-webkit-box')
  })

  it('removes max-height when expanded', () => {
    mockTruncation(true)
    render(<TruncatedContent content="long content" maxLines={2} />)
    const body = screen.getByTestId('truncated-content-body')
    expect(body.style.maxHeight).toBe('2lh')
    fireEvent.click(screen.getByText('Show more'))
    expect(body.style.maxHeight).toBeFalsy()
  })

  it('forwards markdownClassName to MarkdownContent', () => {
    render(<TruncatedContent content="test" markdownClassName="text-base" />)
    const md = screen.getByTestId('markdown-content')
    expect(md.className).toContain('text-base')
  })
})
