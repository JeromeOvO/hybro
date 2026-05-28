import { describe, expect, it } from 'vitest'
import { getPlainTextFromRange } from '@/lib/selection-plain-text'

function textFromHtml(html: string): string {
  const root = document.createElement('div')
  root.innerHTML = html
  const range = document.createRange()
  range.selectNodeContents(root)
  return getPlainTextFromRange(range)
}

describe('getPlainTextFromRange', () => {
  it('preserves line breaks between paragraphs', () => {
    const text = textFromHtml('<p>Line one</p><p>Line two</p>')
    expect(text).toContain('Line one')
    expect(text).toContain('Line two')
    expect(text).toMatch(/Line one\n+Line two/)
  })

  it('preserves list item line breaks', () => {
    const text = textFromHtml('<ul><li>alpha</li><li>beta</li></ul>')
    expect(text).toMatch(/alpha\n+beta/)
  })

  it('handles br elements', () => {
    const text = textFromHtml('a<br>b')
    expect(text).toBe('a\nb')
  })
})
