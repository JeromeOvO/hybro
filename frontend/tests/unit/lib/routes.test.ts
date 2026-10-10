import { describe, expect, it } from 'vitest'

import { routes } from '@/lib/routes'

describe('canonical portal routes', () => {
  it('encodes room and agent identifiers as single path segments', () => {
    expect(routes.room('room/id')).toBe('/room/room%2Fid')
    expect(routes.agent('agent/id')).toBe('/agents/agent%2Fid')
  })
})
