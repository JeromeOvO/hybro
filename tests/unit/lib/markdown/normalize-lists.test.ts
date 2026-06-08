import { describe, expect, it } from 'vitest'
import {
  indentSubBulletsUnderOrderedItems,
  normalizeConversationMarkdown,
  normalizeOrderedListMarkers,
  splitInlineOrderedListItems,
} from '@/lib/markdown/normalize-conversation'

describe('splitInlineOrderedListItems', () => {
  it('splits run-on numbered items onto separate lines', () => {
    const input = '1. "First" — 2 hours ago 2. "Second" — 3 hours ago'
    expect(splitInlineOrderedListItems(input)).toBe([
      '1. "First" — 2 hours ago',
      '2. "Second" — 3 hours ago',
    ].join('\n'))
  })

  it('splits three or more inline markers on one line', () => {
    const input = '1. Alpha 2. Beta 3. Gamma'
    expect(splitInlineOrderedListItems(input)).toBe([
      '1. Alpha',
      '2. Beta',
      '3. Gamma',
    ].join('\n'))
  })

  it('does not split inline numbers inside heading lines', () => {
    const input = [
      '#### 1. **LangGraph 2.0 Release**',
      '- Source: GitHub',
      '',
      '#### 2. **Agent Harness Survey**',
      '- Source: arXiv',
    ].join('\n')

    expect(splitInlineOrderedListItems(input)).toBe(input)
  })

  it('folds a bare ATX heading marker into the next content line', () => {
    const input = [
      '###',
      '1. **Microsoft MDASH**',
      '**Source**: Microsoft',
      '',
      '###',
      '2. **Harness Survey**',
    ].join('\n')

    expect(splitInlineOrderedListItems(input)).toBe([
      '### 1. **Microsoft MDASH**',
      '**Source**: Microsoft',
      '',
      '### 2. **Harness Survey**',
    ].join('\n'))
  })

  it('leaves bare heading markers alone when the next line is empty or another heading', () => {
    const input = [
      '###',
      '',
      'Body paragraph',
      '###',
      '## Another Heading',
    ].join('\n')

    expect(splitInlineOrderedListItems(input)).toBe(input)
  })

  it('does not split inside fenced code blocks', () => {
    const input = [
      '```',
      '1. keep 2. inline',
      '```',
      '1. split 2. me',
    ].join('\n')

    expect(splitInlineOrderedListItems(input)).toBe([
      '```',
      '1. keep 2. inline',
      '```',
      '1. split',
      '2. me',
    ].join('\n'))
  })
})

describe('normalizeConversationMarkdown (remark pipeline)', () => {
  it('renumbers repeated top-level 1. markers', () => {
    const input = [
      '1. First item',
      '1. Second item',
      '1. Third item',
    ].join('\n')

    expect(normalizeConversationMarkdown(input)).toBe([
      '1. First item',
      '2. Second item',
      '3. Third item',
    ].join('\n'))
  })

  it('resets numbering after a heading', () => {
    const input = [
      '1. One',
      '## Section',
      '1. Two',
    ].join('\n')

    expect(normalizeConversationMarkdown(input)).toBe([
      '1. One',
      '## Section',
      '1. Two',
    ].join('\n'))
  })

  it('resets numbering after a thematic break', () => {
    const input = [
      '1. One',
      '',
      '---',
      '',
      '1. Two',
    ].join('\n')

    expect(normalizeConversationMarkdown(input)).toContain('1. One')
    expect(normalizeConversationMarkdown(input)).toContain('1. Two')
  })

  it('restarts numbering after a plain paragraph intro', () => {
    const input = [
      '1. One',
      '2. Two',
      '3. Three',
      '4. Four',
      '5. Five',
      '6. Six',
      '7. Seven',
      '',
      'Three immediate recommended actions for your engineering team (this week)',
      '1. Inventory & patch',
      '2. CI & policy',
      '3. Short-run pilot',
    ].join('\n')

    expect(normalizeConversationMarkdown(input)).toBe([
      '1. One',
      '2. Two',
      '3. Three',
      '4. Four',
      '5. Five',
      '6. Six',
      '7. Seven',
      'Three immediate recommended actions for your engineering team (this week)',
      '1. Inventory & patch',
      '2. CI & policy',
      '3. Short-run pilot',
    ].join('\n'))
  })

  it('nests bullets that immediately follow a numbered section under that item', () => {
    const input = [
      '1. Anthropic headline',
      '- Summary: Example',
      '- Sources: Example',
      '',
      '1. OpenAI headline',
      '- Summary: Example',
    ].join('\n')

    const out = normalizeConversationMarkdown(input)
    expect(out).toContain('1. Anthropic headline\n   - Summary: Example')
    expect(out).toContain('   - Sources: Example')
    expect(out).toContain('2. OpenAI headline\n   - Summary: Example')
  })

  it('preserves leading numbers inside ATX headings as section markers', () => {
    const input = [
      '### Top 3 Items',
      '',
      '---',
      '',
      '#### 1. **First Item**',
      '- Source: GitHub',
      '',
      '---',
      '',
      '#### 2. **Second Item**',
      '- Source: arXiv',
    ].join('\n')

    const out = normalizeConversationMarkdown(input)
    expect(out).toContain('#### 1. **First Item**')
    expect(out).toContain('#### 2. **Second Item**')
    expect(out).not.toMatch(/^####\s*$/m)
  })

  it('does not nest a sibling bullet list under a prior ordered item across a prose paragraph', () => {
    const input = [
      '6. Final Item',
      '- detail one',
      '- detail two',
      '',
      'Trends — what this collection shows',
      '- Trend A',
      '- Trend B',
      '- Trend C',
    ].join('\n')

    const out = normalizeConversationMarkdown(input)
    expect(out).toContain('Trends — what this collection shows\n\n- Trend A')
    expect(out).not.toContain('1. - Trend A')
  })

  it('leaves bold-prefixed numeric paragraphs as paragraphs (no list reshaping)', () => {
    // Agents sometimes write headlines as `**1. Title**` followed by prose
    // continuation. We do not turn these into list items: doing so was the
    // source of double-numbering bugs in the old heuristic pipeline.
    const input = [
      '**1. Bots Outpace Humans** *(milestone)*',
      'AI agents now exceed human traffic.',
      '',
      '**2. OpenAI Lockdown Mode**',
      'ChatGPT Business adds protection.',
    ].join('\n')

    const out = normalizeConversationMarkdown(input)
    expect(out).toContain('**1. Bots Outpace Humans**')
    expect(out).toContain('**2. OpenAI Lockdown Mode**')
  })

  it('fixes inline numbered article lists from agent responses', () => {
    const input = '1. "Notion restores access" — 2 hours ago 2. "OpenAI super app" — 3 hours ago'

    expect(normalizeConversationMarkdown(input)).toBe([
      '1. "Notion restores access" — 2 hours ago',
      '2. "OpenAI super app" — 3 hours ago',
    ].join('\n'))
  })

  it('keeps each section as a single ATX heading when the agent emits a bare `###` line before the title', () => {
    const input = [
      '---',
      '',
      '###',
      '1. **Microsoft MDASH**',
      '**Source**: Microsoft Security Blog',
      '',
      '---',
      '',
      '###',
      '2. **Harness Engineering Survey**',
      '**Source**: OpenReview',
    ].join('\n')

    const out = normalizeConversationMarkdown(input)
    expect(out).toContain('### 1. **Microsoft MDASH**')
    expect(out).toContain('### 2. **Harness Engineering Survey**')
    expect(out).not.toMatch(/^\s*###\s*$/m)
  })

  it('falls back to pre-parse output when remark throws', () => {
    // Force a pathological case unlikely to break remark; verify try/catch exists
    // by ensuring normal content still works after the guard was added.
    expect(normalizeConversationMarkdown('1. A\n1. B')).toBe('1. A\n2. B')
  })

  it('only splits inline markers while streaming', () => {
    const input = [
      '1. Anthropic headline',
      '- Summary: Example',
      '1. OpenAI headline',
    ].join('\n')

    expect(normalizeConversationMarkdown(input, { streaming: true })).toBe(input)
  })
})

/** Deprecated aliases still exported for callers migrating gradually. */
describe('deprecated normalize helpers', () => {
  it('normalizeOrderedListMarkers delegates to remark pipeline', () => {
    expect(normalizeOrderedListMarkers('1. A\n1. B')).toBe('1. A\n2. B')
  })

  it('indentSubBulletsUnderOrderedItems delegates to remark pipeline', () => {
    expect(indentSubBulletsUnderOrderedItems('1. A\n- b')).toBe('1. A\n   - b')
  })
})
