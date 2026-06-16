import { describe, it, expect } from 'vitest'
import { getAgentAvatarUri } from '@/lib/agent-avatar'

describe('getAgentAvatarUri', () => {
  it('returns favicon.svg for system:hybro', () => {
    expect(getAgentAvatarUri('system:hybro')).toBe('/favicon.svg')
  })

  it('returns favicon.svg for system:clarifier', () => {
    expect(getAgentAvatarUri('system:clarifier')).toBe('/favicon.svg')
  })

  it('returns a dicebear data URI for a normal agent seed', () => {
    const uri = getAgentAvatarUri('agent-123')
    expect(uri).toContain('data:image/svg+xml')
    expect(uri).toContain('xmlns')
  })
})
