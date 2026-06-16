import { describe, expect, it } from 'vitest'
import { toString } from 'mdast-util-to-string'
import { unified } from 'unified'
import remarkGfm from 'remark-gfm'
import remarkParse from 'remark-parse'
import {
  applySplitSectionLists,
  promoteSectionLabelParagraphs,
} from '@/lib/markdown/remark-split-section-lists'
import type { Root, RootContent } from 'mdast'

function parseBlocks(content: string): RootContent[] {
  const tree = unified().use(remarkParse).use(remarkGfm).parse(content) as Root
  return tree.children
}

describe('promoteSectionLabelParagraphs', () => {
  it('promotes short labels before a following ordered list', () => {
    const blocks = parseBlocks([
      'TL;DR — Top 3',
      '1. First',
      '',
      'Prioritized items (up to 6)',
      '',
      '1. Second section',
    ].join('\n'))

    const out = promoteSectionLabelParagraphs(blocks)
    expect(out.filter((b) => b.type === 'heading').map((h) => toString(h))).toEqual([
      'TL;DR — Top 3',
      'Prioritized items (up to 6)',
    ])
  })

  it('does not promote long prose intros', () => {
    const intro = 'According to the delegated scans, here is a concise roundup of the most important recent developments in AI agent harness engineering over the last three months.'
    const blocks = parseBlocks([intro, '', '1. First item'].join('\n'))
    const out = promoteSectionLabelParagraphs(blocks)
    expect(out.some((b) => b.type === 'heading')).toBe(false)
  })

  it('does not promote labels followed only by bullet lists', () => {
    const blocks = parseBlocks([
      'Emerging trends',
      '- Trend A',
      '- Trend B',
    ].join('\n'))
    const out = promoteSectionLabelParagraphs(blocks)
    expect(out.some((b) => b.type === 'heading')).toBe(false)
  })
})

describe('applySplitSectionLists', () => {
  it('splits merged supervisor-shaped lists into multiple top-level ordered lists', () => {
    const blocks = parseBlocks([
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

    const out = applySplitSectionLists(blocks)
    const ols = out.filter((b) => b.type === 'list' && b.ordered)
    expect(ols.length).toBeGreaterThanOrEqual(2)
    expect(out.some((b) => b.type === 'heading' && toString(b).includes('Prioritized items'))).toBe(true)
  })
})
