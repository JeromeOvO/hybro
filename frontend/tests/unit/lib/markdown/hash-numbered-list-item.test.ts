import { describe, expect, it } from 'vitest'
import { isHashNumberedListItemText } from '@/lib/markdown/hash-numbered-list-item'

describe('isHashNumberedListItemText', () => {
  it('detects supervisor hash-numbered list item text', () => {
    expect(isHashNumberedListItemText('#1 — Anthropic IPO')).toBe(true)
    expect(isHashNumberedListItemText('  #3 — Tokenpocalypse')).toBe(true)
  })

  it('does not match ordinary numbered headlines', () => {
    expect(isHashNumberedListItemText('MCP release candidate.')).toBe(false)
    expect(isHashNumberedListItemText('LangGraph 2.0 Release')).toBe(false)
  })
})
