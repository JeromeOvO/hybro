import type { Root } from 'mdast'
import { unified } from 'unified'
import remarkParse from 'remark-parse'
import { conversationRemarkPlugins } from '@/lib/markdown/conversation-remark-plugins'

const astProcessor = unified()
  .use(remarkParse)
  .use(conversationRemarkPlugins)

/** Parse conversation markdown and apply the render-time remark plugin pipeline. */
export function processConversationMarkdownAst(content: string): Root {
  const tree = astProcessor.parse(content) as Root
  return astProcessor.runSync(tree) as Root
}

/** Collect `start` values from top-level ordered lists in document order. */
export function topLevelOrderedListStarts(tree: Root): number[] {
  return tree.children
    .filter((block): block is Root['children'][number] & { type: 'list'; ordered: true } =>
      block.type === 'list' && block.ordered === true,
    )
    .map((list) => list.start ?? 1)
}

/** Count list items in each top-level ordered list. */
export function topLevelOrderedListItemCounts(tree: Root): number[] {
  return tree.children
    .filter((block): block is Root['children'][number] & { type: 'list'; ordered: true } =>
      block.type === 'list' && block.ordered === true,
    )
    .map((list) => list.children.length)
}

/** Top-level ordered list item counts flattened in document order. */
export function flattenedTopLevelOrderedNumbers(tree: Root): number[] {
  const nums: number[] = []
  for (const block of tree.children) {
    if (block.type !== 'list' || !block.ordered) continue
    const start = block.start ?? 1
    for (let i = 0; i < block.children.length; i += 1) {
      nums.push(start + i)
    }
  }
  return nums
}
