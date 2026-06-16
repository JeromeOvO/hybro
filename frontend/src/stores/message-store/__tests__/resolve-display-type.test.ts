import { describe, it, expect } from 'vitest'
import { resolveDisplayType } from '../resolve-display-type'

describe('resolveDisplayType', () => {
  it('returns user-bubble for user messages', () => {
    expect(resolveDisplayType({ messageType: 'user' })).toBe('user-bubble')
  })

  it('returns agent-bubble for agent messages', () => {
    expect(resolveDisplayType({ messageType: 'agent' })).toBe('agent-bubble')
  })
})
