import { describe, expect, it } from 'vitest'
import { parsePublicConfig } from '@/lib/config-schema'
import fixture from '../../fixtures/public-config.json'

describe('public JSON configuration', () => {
  it('accepts the CLI projection as an immutable snapshot', () => {
    const result = parsePublicConfig(JSON.stringify(fixture))
    expect(result).toEqual(fixture)
    expect(Object.isFrozen(result)).toBe(true)
  })

  it.each([undefined, '', '[]', '{invalid', JSON.stringify({ ...fixture, clerk_secret_key: 'private-marker' }), JSON.stringify({ ...fixture, max_message_length: -1 }), JSON.stringify({ ...fixture, enable_waitlist: 'true' }), JSON.stringify({ ...fixture, api_base_url: 'https://user:private-marker@example.org' })])('rejects invalid projections without echoing values', value => {
    expect(() => parsePublicConfig(value)).toThrow('frontend configuration')
    try { parsePublicConfig(value) } catch (error) {
      expect(String(error)).not.toContain('private-marker')
    }
  })
})
