import type { List, Root, RootContent } from 'mdast'
import type { Plugin } from 'unified'
import { toString } from 'mdast-util-to-string'

/**
 * Renumber repeated ordered-list `1.` markers and nest a bullet list that
 * immediately follows an ordered list under that list's last item.
 *
 * Why this is small:
 * - Markdown parsing (numbering, indentation, headings, hr) is delegated to
 *   remark + remark-gfm. We do not try to reinterpret prose, headings, or
 *   "section container" words.
 * - The only reshaping we do is the one users universally expect: a `<ul>`
 *   directly following an `<ol>` belongs to the prior numbered item. Any
 *   intervening heading, paragraph, hr, or blank-line-followed-by-prose breaks
 *   that connection automatically because it appears as a sibling block here.
 * - All ordered lists at the root keep continuous numbering across consecutive
 *   sibling lists, but reset after any non-list block (heading, paragraph, hr).
 */

function cloneNode<T>(node: T): T {
  return structuredClone(node)
}

function isPlainNonEmptyParagraph(block: RootContent): boolean {
  return block.type === 'paragraph' && toString(block).trim().length > 0
}

function nestUnorderedListUnderLastOrderedItem(
  ol: List,
  ul: List,
): void {
  const last = ol.children[ol.children.length - 1]
  if (!last) return
  last.children.push(cloneNode(ul))
}

/**
 * Move a `<ul>` that directly follows an `<ol>` (no intervening heading,
 * paragraph, hr, etc.) into the last `<li>` of that `<ol>`.
 */
export function nestAdjacentBulletLists(blocks: RootContent[]): RootContent[] {
  const output: RootContent[] = []

  for (const block of blocks) {
    const prev = output[output.length - 1]
    if (
      block.type === 'list'
      && block.ordered === false
      && prev?.type === 'list'
      && prev.ordered !== false
    ) {
      nestUnorderedListUnderLastOrderedItem(prev, block)
      continue
    }
    output.push(block)
  }

  return output
}

/**
 * Assign a sequential `start` to each top-level ordered list so repeated
 * `1.` markers from agents render as `1, 2, 3 …` across consecutive lists.
 *
 * Reset rules (boundaries that restart numbering at 1):
 *   - any heading
 *   - a thematic break (`---`)
 *   - a non-empty plain paragraph (e.g. an introductory sentence)
 */
export function assignOrderedListStarts(blocks: RootContent[]): RootContent[] {
  let counter = 0

  return blocks.map((block, index) => {
    if (block.type === 'heading' || block.type === 'thematicBreak') {
      counter = 0
      return block
    }

    if (block.type !== 'list' || block.ordered === false) {
      return block
    }

    const prev = (() => {
      for (let j = index - 1; j >= 0; j -= 1) {
        const candidate = blocks[j]
        if (candidate.type === 'paragraph' && !toString(candidate).trim()) continue
        return candidate
      }
      return null
    })()

    if (prev && isPlainNonEmptyParagraph(prev)) {
      counter = 0
    }

    const list = cloneNode(block) as List
    list.start = counter + 1
    counter += list.children.length
    return list
  })
}

export const remarkNestAdjacentBulletLists: Plugin<[], Root> = () => (tree) => {
  try {
    tree.children = nestAdjacentBulletLists(tree.children)
  } catch {
    // Preserve raw render on unexpected mdast shapes.
  }
}

export const remarkAssignOrderedListStarts: Plugin<[], Root> = () => (tree) => {
  try {
    tree.children = assignOrderedListStarts(tree.children)
  } catch {
    // Preserve raw render on unexpected mdast shapes.
  }
}

/** Nest bullets under prior ordered items, then assign sequential list starts. */
export const remarkConversationLists: Plugin<[], Root> = () => (tree) => {
  tree.children = nestAdjacentBulletLists(tree.children)
  tree.children = assignOrderedListStarts(tree.children)
}
