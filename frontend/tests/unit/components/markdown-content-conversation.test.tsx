import { readFileSync } from 'node:fs'
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
  it('conversation tokens set table text to 14px', () => {
    const tokens = readFileSync('src/components/conversation/conversation-tokens.css', 'utf8')
    expect(tokens).toContain('--conversation-content-table-font-size: 0.875rem;')
    expect(tokens).toContain('.conversation-markdown-body :where(table)')
  })

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

  itIfStreamdown('removes sandbox room-file destinations while preserving their label', () => {
    const { container } = render(
      <MarkdownContent
        className="conversation-markdown-body"
        content="Image: [Open the image here](sandbox:/api/v1/files/hallucinated/content)"
      />,
    )

    expect(container).toHaveTextContent('Image: Open the image here')
    expect(container).not.toHaveTextContent('blocked')
    expect(container.querySelector('a')).toBeNull()
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

  itIfStreamdown('restarts ordered-list numbering after supervisor section labels', () => {
    const { container } = render(
      <div className="conversation-content-body">
        <MarkdownContent
          className="conversation-markdown-body"
          content={[
            'TL;DR — Top 3',
            '1. MCP release candidate.',
            '2. OpenAI Agents SDK.',
            '3. Anthropic Teaching Claude Why.',
            '',
            'Prioritized items (up to 6)',
            '',
            '1. MCP (Model Context Protocol) — release candidate',
            '- **Summary:** MCP RC.',
            '- **Paywall:** No',
            '',
            '2. OpenAI Agents SDK',
            '- **Summary:** SDK update.',
          ].join('\n')}
        />
      </div>,
    )

    const lists = container.querySelectorAll('.conversation-markdown-body ol:not(ol ol)')
    expect(lists.length).toBeGreaterThanOrEqual(2)

    const secondListItems = lists[1]?.querySelectorAll(':scope > li')
    expect(secondListItems?.length).toBe(2)
    expect(secondListItems?.[0]?.textContent).toContain('MCP (Model Context Protocol)')

    if (typeof CSS !== 'undefined' && CSS.supports?.('selector', 'ol ::before')) {
      const firstMarker = window.getComputedStyle(
        secondListItems![0]!,
        '::before',
      ).content.replace(/"/g, '')
      expect(firstMarker).toMatch(/^1\.\s/)
    }
  })

  itIfStreamdown('restarts ordered-list numbering when supervisor sections use ### headings', () => {
    const { container } = render(
      <div className="conversation-content-body">
        <MarkdownContent
          className="conversation-markdown-body"
          content={[
            '### TL;DR — Top 3',
            '1. MCP release candidate.',
            '2. OpenAI Agents SDK.',
            '',
            '### Prioritized items (up to 6)',
            '1. MCP (Model Context Protocol) — release candidate',
            '- **Summary:** MCP RC.',
            '',
            '2. OpenAI Agents SDK',
            '- **Summary:** SDK update.',
          ].join('\n')}
        />
      </div>,
    )

    const lists = container.querySelectorAll('.conversation-markdown-body ol:not(ol ol)')
    expect(lists.length).toBeGreaterThanOrEqual(2)

    const secondListItems = lists[1]?.querySelectorAll(':scope > li')
    expect(secondListItems?.length).toBe(2)

    if (typeof CSS !== 'undefined' && CSS.supports?.('selector', 'ol ::before')) {
      const firstMarker = window.getComputedStyle(
        secondListItems![0]!,
        '::before',
      ).content.replace(/"/g, '')
      expect(firstMarker).toMatch(/^1\.\s/)
    }
  })

  itIfStreamdown('keeps full supervisor shape numbered 1–3, 1–6, 1–3 across sections', () => {
    const { container } = render(
      <div className="conversation-content-body">
        <MarkdownContent
          className="conversation-markdown-body"
          content={[
            'TL;DR — Top 3',
            '1. MCP release candidate.',
            '2. OpenAI Agents SDK.',
            '3. Anthropic Teaching Claude Why.',
            '',
            'Prioritized items (up to 6)',
            '',
            '1. MCP (Model Context Protocol) — release candidate',
            '- **Summary:** MCP RC.',
            '- **Paywall:** No',
            '',
            '2. OpenAI Agents SDK',
            '- **Summary:** SDK update.',
            '- **Paywall:** No',
            '',
            '3. Teaching Claude Why',
            '- **Summary:** Anthropic research.',
            '- **Paywall:** No',
            '',
            '4. LangChain patterns',
            '- **Summary:** middleware-first.',
            '- **Paywall:** No',
            '',
            '5. Google ADK Go 1.0',
            '- **Summary:** ADK updates.',
            '- **Paywall:** No',
            '',
            '6. Terminal coding agents paper',
            '- **Summary:** two-phase approach.',
            '- **Paywall:** No',
            '',
            'Recommended next actions (specific)',
            '1. Experiment with MCP RC.',
            '2. Prototype a sandboxed harness.',
            '3. Add harness-level tests.',
          ].join('\n')}
        />
      </div>,
    )

    const lists = container.querySelectorAll('.conversation-markdown-body ol:not(ol ol)')
    expect(lists.length).toBeGreaterThanOrEqual(3)
    expect(lists[0]?.querySelectorAll(':scope > li').length).toBe(3)
    expect(lists[1]?.querySelectorAll(':scope > li').length).toBe(6)
    expect(lists[2]?.querySelectorAll(':scope > li').length).toBe(3)

    if (typeof CSS !== 'undefined' && CSS.supports?.('selector', 'ol ::before')) {
      const prioritizedFirst = lists[1]?.querySelector(':scope > li')
      const marker = window.getComputedStyle(
        prioritizedFirst!,
        '::before',
      ).content.replace(/"/g, '')
      expect(marker).toMatch(/^1\.\s/)
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

  itIfStreamdown('renders GFM tables in conversation markdown', () => {
    const { container } = render(
      <div className="conversation-content-body">
        <MarkdownContent
          className="conversation-markdown-body"
          content={[
            '| Agent | Status |',
            '| --- | --- |',
            '| MCP | Active |',
            '| SDK | Beta |',
          ].join('\n')}
        />
      </div>,
    )

    const table = container.querySelector('.conversation-markdown-body table')
    expect(table).toBeTruthy()
    const headers = container.querySelectorAll('.conversation-markdown-body th')
    const cells = container.querySelectorAll('.conversation-markdown-body td')
    expect(headers.length).toBe(2)
    expect(cells.length).toBe(4)
    expect(table?.textContent).toContain('MCP')
    expect(table?.textContent).toContain('Beta')
  })

  itIfStreamdown('hides CSS list counters when items already include #N markers', () => {
    const { container } = render(
      <div className="conversation-content-body">
        <MarkdownContent
          className="conversation-markdown-body"
          content={[
            '### Top 3 Must-Reads',
            '1. **#1 — Anthropic IPO** summary text.',
            '2. **#2 — OpenAI Super App** summary text.',
            '3. **#3 — Tokenpocalypse** summary text.',
          ].join('\n')}
        />
      </div>,
    )

    const items = container.querySelectorAll('.conversation-markdown-body ol:not(ol ol) > li')
    expect(items.length).toBe(3)
    expect(items[0]?.classList.contains('conv-hash-numbered-item')).toBe(true)
    expect(items[1]?.classList.contains('conv-hash-numbered-item')).toBe(true)
    expect(items[2]?.classList.contains('conv-hash-numbered-item')).toBe(true)

    if (typeof CSS !== 'undefined' && CSS.supports?.('selector', 'ol ::before')) {
      const marker = window.getComputedStyle(items[0]!, '::before').content.replace(/"/g, '')
      expect(marker).toBe('none')
    }
  })

  itIfStreamdown('keeps CSS list counters for ordinary numbered items', () => {
    const { container } = render(
      <div className="conversation-content-body">
        <MarkdownContent
          className="conversation-markdown-body"
          content={[
            '1. MCP release candidate.',
            '2. OpenAI Agents SDK.',
          ].join('\n')}
        />
      </div>,
    )

    const items = container.querySelectorAll('.conversation-markdown-body ol:not(ol ol) > li')
    expect(items[0]?.classList.contains('conv-hash-numbered-item')).toBe(false)

    if (typeof CSS !== 'undefined' && CSS.supports?.('selector', 'ol ::before')) {
      const marker = window.getComputedStyle(items[0]!, '::before').content.replace(/"/g, '')
      expect(marker).toMatch(/^1\.\s/)
    }
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
