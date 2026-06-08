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

  itIfStreamdown('renumbers repeated ordered-list markers in conversation markdown', () => {
    const { container } = render(
      <div className="conversation-content-body">
        <MarkdownContent
          className="conversation-markdown-body"
          content={[
            '## Top AI News',
            '1. Anthropic headline',
            '- Summary: Example',
            '',
            '1. OpenAI headline',
            '- Summary: Example',
          ].join('\n')}
        />
      </div>,
    )

    const topLevelItems = container.querySelectorAll('.conversation-markdown-body ol > li')
    expect(topLevelItems.length).toBeGreaterThanOrEqual(2)
    expect(topLevelItems[0]?.textContent).toContain('Anthropic headline')
    expect(topLevelItems[1]?.textContent).toContain('OpenAI headline')
  })

  itIfStreamdown('renders inline numbered items as separate list rows', () => {
    const { container } = render(
      <div className="conversation-content-body">
        <MarkdownContent
          className="conversation-markdown-body"
          content={'1. "Notion restores access" — 2 hours ago 2. "OpenAI super app" — 3 hours ago'}
        />
      </div>,
    )

    const items = container.querySelectorAll('.conversation-markdown-body ol > li')
    expect(items.length).toBe(2)
    expect(items[0]?.textContent).toContain('Notion restores access')
    expect(items[1]?.textContent).toContain('OpenAI super app')
  })

  itIfStreamdown('numbers repeated headline markers sequentially in one section', () => {
    const { container } = render(
      <div className="conversation-content-body">
        <MarkdownContent
          className="conversation-markdown-body"
          content={[
            '1. LangGraph 2.0 Release',
            '- Source & URL: example',
            '',
            '1. Agent Harness Engineering',
            '- Source & URL: example',
            '',
            '1. Awesome List',
            '- Source & URL: example',
          ].join('\n')}
        />
      </div>,
    )

    const topLevelItems = container.querySelectorAll('.conversation-markdown-body ol:not(ol ol) > li')
    expect(topLevelItems.length).toBeGreaterThanOrEqual(3)
    expect(topLevelItems[0]?.textContent).toContain('LangGraph')
    expect(topLevelItems[1]?.textContent).toContain('Agent Harness')
    expect(topLevelItems[2]?.textContent).toContain('Awesome List')

    if (typeof CSS !== 'undefined' && CSS.supports?.('selector', 'ol ::before')) {
      const markers = Array.from(topLevelItems).map(
        (li) => getComputedStyle(li, '::before').content.replace(/"/g, ''),
      )
      expect(markers[0]).toMatch(/^1\.\s/)
      expect(markers[1]).toMatch(/^2\.\s/)
      expect(markers[2]).toMatch(/^3\.\s/)
    }
  })

  itIfStreamdown('restarts ordered-list numbering after hr and section intro', () => {
    const { container } = render(
      <div className="conversation-content-body">
        <MarkdownContent
          className="conversation-markdown-body"
          content={[
            '1. First headline',
            '2. Second headline',
            '3. Third headline',
            '4. Fourth headline',
            '5. Fifth headline',
            '6. Sixth headline',
            '7. Seventh headline',
            '8. Eighth headline',
            '',
            '---',
            '',
            '**Major Trends in AI Agent Harness Engineering**',
            '',
            '9. Shift from Prompt to Harness Engineering',
            '10. Code as a First-Class Operational Medium',
            '11. Standardization and Protocolization',
          ].join('\n')}
        />
      </div>,
    )

    const lists = container.querySelectorAll('.conversation-markdown-body ol:not(ol ol)')
    expect(lists.length).toBeGreaterThanOrEqual(2)

    const secondListItems = lists[1]?.querySelectorAll(':scope > li')
    expect(secondListItems?.length).toBe(3)
    expect(secondListItems?.[0]?.textContent).toContain('Shift from Prompt')
    expect(secondListItems?.[1]?.textContent).toContain('Code as a First-Class')

    if (typeof CSS !== 'undefined' && CSS.supports?.('selector', 'ol ::before')) {
      const firstMarker = window.getComputedStyle(
        secondListItems![0]!,
        '::before',
      ).content.replace(/"/g, '')
      expect(firstMarker).toMatch(/^1\.\s/)
    }
  })

  itIfStreamdown('folds bare ATX heading markers into headings instead of empty h3 + ol', () => {
    const { container } = render(
      <div className="conversation-content-body">
        <MarkdownContent
          className="conversation-markdown-body"
          content={[
            '---',
            '',
            '###',
            '1. **Microsoft MDASH**',
            '**Source**: Microsoft Security Blog',
            '',
            '---',
            '',
            '###',
            '2. **Harness Engineering Survey**',
            '**Source**: OpenReview',
          ].join('\n')}
        />
      </div>,
    )

    const headings = container.querySelectorAll('.conversation-markdown-body h3')
    expect(headings.length).toBeGreaterThanOrEqual(2)
    expect(headings[0]?.textContent).toContain('Microsoft MDASH')
    expect(headings[1]?.textContent).toContain('Harness Engineering Survey')
    expect(container.querySelectorAll('.conversation-markdown-body h3:empty').length).toBe(0)
    expect(container.querySelectorAll('.conversation-markdown-body ol').length).toBe(0)
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
