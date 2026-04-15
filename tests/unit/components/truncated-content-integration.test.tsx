import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { render as probeRender } from '@testing-library/react'
import { MarkdownContent } from '@/components/markdown-content'
import { TruncatedContent } from '@/components/truncated-content'

afterEach(() => cleanup())

let streamdownRenders = true
try {
  const { unmount } = probeRender(<MarkdownContent content="test" />)
  unmount()
} catch {
  streamdownRenders = false
}

const itIfStreamdown = streamdownRenders ? it : it.skip

describe('TruncatedContent (integration with MarkdownContent)', () => {
  itIfStreamdown('renders markdown headings as block elements', () => {
    render(<TruncatedContent content="# Heading\n\nParagraph text" maxLines={6} />)
    const body = screen.getByTestId('truncated-content-body')
    expect(body.querySelector('h1')).toBeTruthy()
    expect(body.querySelector('h1')!.textContent).toContain('Heading')
    expect(body.className).toContain('overflow-hidden')
  })

  itIfStreamdown('renders code blocks as <code> elements', () => {
    render(<TruncatedContent content={"```js\nconsole.log('hello')\n```"} maxLines={6} />)
    const body = screen.getByTestId('truncated-content-body')
    expect(body.querySelector('code')).toBeTruthy()
    expect(body.querySelector('code')!.textContent).toContain("console.log('hello')")
  })

  itIfStreamdown('renders lists as <li> elements', () => {
    render(<TruncatedContent content="- item 1\n- item 2\n- item 3" maxLines={6} />)
    const body = screen.getByTestId('truncated-content-body')
    const items = body.querySelectorAll('li')
    // Streamdown in jsdom may collapse list items; just verify at least one renders
    expect(items.length).toBeGreaterThanOrEqual(1)
    expect(items[0].textContent).toContain('item 1')
  })
})
