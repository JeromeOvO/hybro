import type {
  Heading,
  List,
  ListItem,
  Paragraph,
  Root,
  RootContent,
} from 'mdast'
import type { Plugin } from 'unified'
import { toString } from 'mdast-util-to-string'
import { isSectionLabelText } from '@/lib/markdown/section-label'

function cloneNode<T>(node: T): T {
  return structuredClone(node)
}

function paragraphToHeading(paragraph: Paragraph): Heading {
  return {
    type: 'heading',
    depth: 3,
    children: cloneNode(paragraph.children),
  }
}

function findNextOrderedListIndex(blocks: RootContent[], fromIndex: number): number {
  for (let i = fromIndex; i < blocks.length; i += 1) {
    const block = blocks[i]
    if (block.type === 'list' && block.ordered) return i
    if (block.type !== 'paragraph' || toString(block).trim()) break
  }
  return -1
}

/** Promote short paragraphs that introduce a following ordered list to h3. */
function promoteSectionLabelParagraphs(blocks: RootContent[]): RootContent[] {
  const output: RootContent[] = []

  for (let i = 0; i < blocks.length; i += 1) {
    const block = blocks[i]
    if (
      block.type === 'paragraph'
      && isSectionLabelText(toString(block))
      && findNextOrderedListIndex(blocks, i + 1) !== -1
    ) {
      output.push(paragraphToHeading(block))
      continue
    }
    output.push(block)
  }

  return output
}

function trailingSectionLabelParagraph(item: ListItem): Paragraph | null {
  const paragraphs = item.children.filter((c): c is Paragraph => c.type === 'paragraph')
  if (paragraphs.length < 2) return null
  const last = paragraphs[paragraphs.length - 1]
  if (item.children.some((c) => c.type === 'list')) return null
  return isSectionLabelText(toString(last)) ? last : null
}

function trimTrailingSectionLabel(item: ListItem, trailing: Paragraph): ListItem {
  const trimmed = cloneNode(item)
  const index = trimmed.children.lastIndexOf(trailing)
  if (index !== -1) trimmed.children.splice(index, 1)
  return trimmed
}

/** Split one root ordered list when a list item ends with a section-label paragraph. */
function splitOrderedListAtSectionLabels(list: List): RootContent[] {
  if (!list.ordered || list.children.length < 2) return [list]

  const sections: RootContent[] = []
  let batch: ListItem[] = []

  const flushBatch = () => {
    if (batch.length === 0) return
    sections.push({
      type: 'list',
      ordered: true,
      spread: list.spread,
      children: batch,
    })
    batch = []
  }

  for (let i = 0; i < list.children.length; i += 1) {
    const item = list.children[i]
    const trailing = trailingSectionLabelParagraph(item)

    if (trailing && i < list.children.length - 1) {
      const trimmed = trimTrailingSectionLabel(item, trailing)
      if (trimmed.children.length > 0) batch.push(trimmed)
      flushBatch()
      sections.push(paragraphToHeading(trailing))
      continue
    }

    batch.push(item)
  }

  flushBatch()
  return sections.length > 1 ? sections : [list]
}

function splitMegaListsInBlocks(blocks: RootContent[]): RootContent[] {
  const output: RootContent[] = []

  for (const block of blocks) {
    if (block.type === 'list' && block.ordered) {
      output.push(...splitOrderedListAtSectionLabels(block))
      continue
    }
    output.push(block)
  }

  return output
}

function applySplitSectionLists(blocks: RootContent[]): RootContent[] {
  return splitMegaListsInBlocks(promoteSectionLabelParagraphs(blocks))
}

/**
 * Promote prose section labels to headings and split merged ordered lists so
 * each numbered section can restart at 1. Run before nest/assign plugins.
 */
export const remarkSplitSectionLists: Plugin<[], Root> = () => (tree) => {
  try {
    tree.children = applySplitSectionLists(tree.children)
  } catch {
    // Preserve raw render on unexpected mdast shapes.
  }
}

export {
  applySplitSectionLists,
  promoteSectionLabelParagraphs,
  splitOrderedListAtSectionLabels,
}
