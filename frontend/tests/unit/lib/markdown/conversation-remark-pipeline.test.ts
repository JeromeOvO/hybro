import { describe, expect, it } from 'vitest'
import { toString } from 'mdast-util-to-string'
import {
  flattenedTopLevelOrderedNumbers,
  processConversationMarkdownAst,
  topLevelOrderedListItemCounts,
  topLevelOrderedListStarts,
} from '@/lib/markdown/conversation-remark-pipeline'

describe('processConversationMarkdownAst', () => {
  it('assigns sequential starts across consecutive top-level ordered lists', () => {
    const tree = processConversationMarkdownAst([
      '1. First item',
      '1. Second item',
      '1. Third item',
    ].join('\n'))

    expect(topLevelOrderedListStarts(tree)).toEqual([1])
    expect(topLevelOrderedListItemCounts(tree)).toEqual([3])
    expect(flattenedTopLevelOrderedNumbers(tree)).toEqual([1, 2, 3])
  })

  it('resets numbering after a heading', () => {
    const tree = processConversationMarkdownAst([
      '1. One',
      '## Section',
      '1. Two',
    ].join('\n'))

    expect(topLevelOrderedListStarts(tree)).toEqual([1, 1])
    expect(flattenedTopLevelOrderedNumbers(tree)).toEqual([1, 1])
  })

  it('resets numbering after a thematic break', () => {
    const tree = processConversationMarkdownAst([
      '1. One',
      '',
      '---',
      '',
      '1. Two',
    ].join('\n'))

    expect(topLevelOrderedListStarts(tree)).toEqual([1, 1])
  })

  it('restarts numbering after a plain paragraph intro', () => {
    const tree = processConversationMarkdownAst([
      '1. One',
      '2. Two',
      '3. Three',
      '',
      'Three immediate recommended actions for your engineering team (this week)',
      '1. Inventory & patch',
      '2. CI & policy',
      '3. Short-run pilot',
    ].join('\n'))

    expect(topLevelOrderedListStarts(tree)).toEqual([1, 1])
    expect(flattenedTopLevelOrderedNumbers(tree)).toEqual([1, 2, 3, 1, 2, 3])
  })

  it('nests bullets that immediately follow a numbered section under that item', () => {
    const tree = processConversationMarkdownAst([
      '1. Anthropic headline',
      '- Summary: Example',
      '- Sources: Example',
      '',
      '1. OpenAI headline',
      '- Summary: Example',
    ].join('\n'))

    const firstOl = tree.children.find((b) => b.type === 'list' && b.ordered)
    expect(firstOl?.type).toBe('list')
    if (firstOl?.type !== 'list') return
    expect(firstOl.children).toHaveLength(2)
    const firstItem = firstOl.children[0]
    expect(firstItem.children.some((c) => c.type === 'list')).toBe(true)
  })

  it('does not nest bullets across a prose paragraph boundary', () => {
    const tree = processConversationMarkdownAst([
      '6. Final Item',
      '- detail one',
      '- detail two',
      '',
      'Trends — what this collection shows',
      '- Trend A',
      '- Trend B',
    ].join('\n'))

    const ols = tree.children.filter((b) => b.type === 'list' && b.ordered)
    expect(ols.length).toBeGreaterThanOrEqual(1)
    const firstOl = ols[0]
    if (firstOl.type !== 'list') return
    const lastItem = firstOl.children[firstOl.children.length - 1]
    expect(lastItem.children.some((c) => c.type === 'list')).toBe(true)
    const trendsBlock = tree.children.find(
      (b) => b.type === 'paragraph' && toString(b).includes('Trends'),
    )
    expect(trendsBlock).toBeTruthy()
  })

  it('keeps full supervisor prioritized section numbered 1–6 after TL;DR', () => {
    const tree = processConversationMarkdownAst([
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
    ].join('\n'))

    expect(flattenedTopLevelOrderedNumbers(tree)).toEqual([1, 2, 3, 1, 2, 3, 4, 5, 6, 1, 2, 3])
    expect(topLevelOrderedListStarts(tree).length).toBeGreaterThanOrEqual(3)
  })

  it('restarts numbering after supervisor section labels', () => {
    const tree = processConversationMarkdownAst([
      'TL;DR — Top 3',
      '1. MCP release candidate.',
      '2. OpenAI Agents SDK.',
      '3. Anthropic Teaching Claude Why.',
      '',
      'Prioritized items (up to 6)',
      '',
      '1. MCP (Model Context Protocol) — release candidate',
      '- **Summary:** MCP RC.',
      '',
      '2. OpenAI Agents SDK',
      '- **Summary:** SDK update.',
    ].join('\n'))

    expect(flattenedTopLevelOrderedNumbers(tree)).toEqual([1, 2, 3, 1, 2])
    expect(topLevelOrderedListStarts(tree)).toEqual([1, 1])
  })

  it('works when section labels already use ### headings', () => {
    const tree = processConversationMarkdownAst([
      '### TL;DR — Top 3',
      '1. MCP release candidate.',
      '2. OpenAI Agents SDK.',
      '',
      '### Prioritized items (up to 6)',
      '1. MCP (Model Context Protocol) — release candidate',
      '2. OpenAI Agents SDK',
    ].join('\n'))

    expect(flattenedTopLevelOrderedNumbers(tree)).toEqual([1, 2, 1, 2])
    expect(topLevelOrderedListStarts(tree)).toEqual([1, 1])
  })
})
