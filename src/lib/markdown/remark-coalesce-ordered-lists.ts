import type { List, Root, RootContent } from 'mdast'
import type { Plugin } from 'unified'

function cloneNode<T>(node: T): T {
  return structuredClone(node)
}

function isOrderedList(block: RootContent): block is List {
  return block.type === 'list' && block.ordered !== false
}

/** Merge consecutive top-level ordered lists (e.g. after blank lines break one section). */
export function coalesceOrderedListBlocks(blocks: RootContent[]): RootContent[] {
  const output: RootContent[] = []

  for (const block of blocks) {
    const prev = output[output.length - 1]
    if (isOrderedList(block) && prev && isOrderedList(prev)) {
      prev.children.push(...cloneNode(block.children))
      continue
    }
    output.push(cloneNode(block))
  }

  return output
}

export const remarkCoalesceOrderedLists: Plugin<[], Root> = () => (tree) => {
  try {
    tree.children = coalesceOrderedListBlocks(tree.children)
  } catch {
    // Preserve raw render on unexpected mdast shapes.
  }
}
