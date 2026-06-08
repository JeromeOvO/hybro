import { describe, expect, it } from 'vitest'
import { unified } from 'unified'
import remarkParse from 'remark-parse'
import remarkGfm from 'remark-gfm'
import { toString } from 'mdast-util-to-string'
import { coalesceOrderedListBlocks } from '@/lib/markdown/remark-coalesce-ordered-lists'
import { nestAdjacentBulletLists } from '@/lib/markdown/remark-conversation-lists'

describe('coalesceOrderedListBlocks', () => {
  it('merges fragmented prioritized lists and nests summary lines', () => {
    const tree = unified().use(remarkParse).use(remarkGfm).parse([
      'Prioritized items (up to 6)',
      '',
      '1. MCP (Model Context Protocol) — release candidate',
      '- **Summary:** MCP RC.',
      '',
      '2. OpenAI Agents SDK',
      '- **Summary:** SDK update.',
    ].join('\n'))

    const out = coalesceOrderedListBlocks(nestAdjacentBulletLists(tree.children))
    const lists = out.filter((b) => b.type === 'list' && b.ordered !== false)
    expect(lists).toHaveLength(1)
    if (lists[0]?.type !== 'list') return
    expect(lists[0].children).toHaveLength(2)
    expect(toString(lists[0].children[0])).toContain('MCP (Model Context Protocol)')
    expect(lists[0].children[0].children.some((c) => c.type === 'list')).toBe(true)
  })
})
