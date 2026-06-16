import type { PluggableList } from 'unified'
import remarkGfm from 'remark-gfm'
import { remarkCoalesceOrderedLists } from '@/lib/markdown/remark-coalesce-ordered-lists'
import {
  remarkAssignOrderedListStarts,
  remarkNestAdjacentBulletLists,
} from '@/lib/markdown/remark-conversation-lists'
import { remarkSplitSectionLists } from '@/lib/markdown/remark-split-section-lists'

/** List/section surgery only — run after remark-gfm. */
export const conversationListRemarkPlugins: PluggableList = [
  remarkSplitSectionLists,
  remarkNestAdjacentBulletLists,
  remarkCoalesceOrderedLists,
  remarkAssignOrderedListStarts,
]

/**
 * Full Streamdown remark bundle for conversation markdown.
 * Streamdown replaces its default plugins when `remarkPlugins` is set, so we must
 * include remark-gfm here (tables, strikethrough, task lists, etc.).
 */
export const conversationRemarkPlugins: PluggableList = [
  remarkGfm,
  ...conversationListRemarkPlugins,
]
