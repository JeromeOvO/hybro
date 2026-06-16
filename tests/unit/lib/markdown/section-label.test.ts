import { describe, expect, it } from 'vitest'
import { isSectionLabelText, SECTION_LABEL_MAX_LENGTH } from '@/lib/markdown/section-label'

describe('isSectionLabelText', () => {
  it('accepts short supervisor-style section labels', () => {
    expect(isSectionLabelText('TL;DR — Top 3')).toBe(true)
    expect(isSectionLabelText('Prioritized items (up to 6)')).toBe(true)
    expect(isSectionLabelText('Recommended next actions (specific)')).toBe(true)
  })

  it('rejects list markers, bullets, and headings', () => {
    expect(isSectionLabelText('1. First item')).toBe(false)
    expect(isSectionLabelText('- bullet')).toBe(false)
    expect(isSectionLabelText('### Already a heading')).toBe(false)
    expect(isSectionLabelText('**1. Bold headline**')).toBe(false)
  })

  it('rejects long prose intros', () => {
    const intro = 'x'.repeat(SECTION_LABEL_MAX_LENGTH + 1)
    expect(isSectionLabelText(intro)).toBe(false)
  })

  it('rejects empty strings', () => {
    expect(isSectionLabelText('')).toBe(false)
    expect(isSectionLabelText('   ')).toBe(false)
  })
})
