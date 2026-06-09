import { describe, expect, it } from 'vitest'
import {
  preprocessConversationMarkdown,
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

  it('does not promote section labels in pre-parse (handled at render-time mdast)', () => {
    const input = [
      'TL;DR — Top 3',
      '1. First',
      '',
      'Prioritized items (up to 6)',
      '',
      '1. Second section',
    ].join('\n')

    expect(splitInlineOrderedListItems(input)).toBe(input)
  })

  it('does not split prose "in 4. The" inside hash-numbered supervisor list items', () => {
    const input = [
      '3. **#3 — Tokenpocalypse / Token Cost Reckoning:** The most structurally important story for enterprise AI adoption in 4. The era of unlimited AI spend is ending — expect repricing, consolidation, and new tooling standards.',
    ].join('\n')

    expect(splitInlineOrderedListItems(input)).toBe(input)
  })

  it('still splits run-on hash-numbered supervisor items onto separate lines', () => {
    const input = '1. **#1 — Alpha** summary 2. **#2 — Beta** summary'
    expect(splitInlineOrderedListItems(input)).toBe([
      '1. **#1 — Alpha** summary',
      '2. **#2 — Beta** summary',
    ].join('\n'))
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

  it('does not split prose sentences that mention numbered steps', () => {
    const input = 'See step 1. For details 2. For more context on the rollout.'
    expect(splitInlineOrderedListItems(input)).toBe(input)
  })

  it('skips inline ordered splits while streaming', () => {
    const input = '1. "First" — 2 hours ago 2. "Second" — 3 hours ago'
    expect(splitInlineOrderedListItems(input, { streaming: true })).toBe(input)
  })
})

describe('preprocessConversationMarkdown', () => {
  it('fixes inline numbered article lists from agent responses', () => {
    const input = '1. "Notion restores access" — 2 hours ago 2. "OpenAI super app" — 3 hours ago'

    expect(preprocessConversationMarkdown(input)).toBe([
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

    const out = preprocessConversationMarkdown(input)
    expect(out).toContain('### 1. **Microsoft MDASH**')
    expect(out).toContain('### 2. **Harness Engineering Survey**')
    expect(out).not.toMatch(/^\s*###\s*$/m)
  })

  it('does not change multi-line repeated list markers while streaming', () => {
    const input = [
      '1. Anthropic headline',
      '- Summary: Example',
      '1. OpenAI headline',
    ].join('\n')

    expect(preprocessConversationMarkdown(input, { streaming: true })).toBe(input)
  })
})
