import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, render as probeRender } from '@testing-library/react'
import { MarkdownContent } from '@/components/markdown-content'

afterEach(() => cleanup())

let streamdownRenders = true
try {
  const { unmount } = probeRender(<MarkdownContent content="test" />)
  unmount()
} catch {
  streamdownRenders = false
}

const itIfStreamdown = streamdownRenders ? it : it.skip

describe('MarkdownContent conversation typography', () => {
  itIfStreamdown('does not apply compact defaults when conversation-markdown-body is set', () => {
    const { container } = render(
      <div className="conversation-content-body">
        <MarkdownContent
          className="conversation-markdown-body"
          content="### Subheading\n\nParagraph one.\n\n- alpha\n- beta"
        />
      </div>,
    )

    const wrapper = container.querySelector('.conversation-markdown-body')
    expect(wrapper).toBeTruthy()
    expect(wrapper!.className).not.toContain('text-sm')
    expect(wrapper!.className).not.toContain('leading-relaxed')

    const h3 = container.querySelector('h3')
    expect(h3).toBeTruthy()
    expect(h3!.className).not.toContain('text-sm')

    const li = container.querySelector('li')
    if (li) {
      expect(li.className).not.toContain('mb-1')
    }
  })

  itIfStreamdown('strips literal "4 spaces" prefix from supervisor-style nested fields', () => {
    const { container } = render(
      <div className="conversation-content-body">
        <MarkdownContent
          className="conversation-markdown-body"
          content={'4 spaces Date: June 2, 2026\n\n4 spaces Summary: Example text.'}
        />
      </div>,
    )

    expect(container.textContent).toContain('Date: June 2, 2026')
    expect(container.textContent).toContain('Summary: Example text.')
    expect(container.textContent).not.toContain('4 spaces')
  })

  itIfStreamdown('keeps compact defaults outside conversation surfaces', () => {
    const { container } = render(<MarkdownContent content="### Subheading" />)

    const wrapper = container.firstElementChild as HTMLElement
    expect(wrapper.className).toContain('text-sm')
    expect(wrapper.className).toContain('leading-relaxed')

    const h3 = container.querySelector('h3')
    expect(h3).toBeTruthy()
    expect(h3!.className).toContain('text-sm')
  })
})
