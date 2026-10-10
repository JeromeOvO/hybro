import { describe, expect, it } from 'vitest'
import { buildAccessMaterial } from '@/components/access/access-material'

describe('access materials', () => {

  it('omits group_id for all-agent discovery', () => {
    const material = buildAccessMaterial('api', 'https://hybro.example', '/custom/api')
    expect(material.configuration).toContain('https://hybro.example/custom/api/agents/discovery')
    expect(material.configuration).not.toContain('group_id')
    expect(material.configuration).not.toMatch(/authorization|bearer|access_token/i)
  })

  it('safely quotes the configured API address for the shell', () => {
    const material = buildAccessMaterial('api', "https://hybro.example/it's", '/api/v1')
    expect(material.configuration).toContain(`it'"'"'s`)
    expect(material.configuration).not.toMatch(/authorization|bearer|access_token/i)
  })

})
