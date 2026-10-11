import { describe, expect, it } from 'vitest'
import { buildAccessMaterial } from '@/components/connect/access-material'
import { buildMcpConfig } from '@/lib/mcp'

const scope = { id: 'g1', name: 'Research' }

describe('access materials', () => {
  it('omits group_id for whole-network discovery', () => {
    const material = buildAccessMaterial('api', 'https://hybro.example', '/custom/api')
    expect(material.configuration).toContain('https://hybro.example/custom/api/agents/discovery')
    expect(material.configuration).not.toContain('group_id')
    expect(material.configuration).not.toMatch(/authorization|bearer|access_token/i)
  })

  it('safely quotes the configured API address for the shell', () => {
    const material = buildAccessMaterial('api', "https://hybro.example/it's", '/api/v1')
    expect(material.configuration).toContain(`it'"'"'s`)
  })

  it('never puts the scope in the MCP connection configuration', () => {
    const url = 'http://127.0.0.1:8001/mcp'
    expect(buildAccessMaterial('mcp', url, '/api/v1', scope).configuration).toBe(buildMcpConfig(url))
    expect(buildAccessMaterial('mcp', url, '/api/v1', scope).configuration).not.toContain('g1')
  })

  it('scopes the MCP prompt and tool calls per call', () => {
    const material = buildAccessMaterial('mcp', 'http://127.0.0.1:8001/mcp', '/api/v1', scope)
    expect(material.prompt).toContain('Research')
    expect(material.prompt).toContain('group_id "g1"')
    expect(material.prompt).toContain('every discover_agents and send_agent_message call')
    expect(material.prompt).toContain('within a subnet')
    expect(material.toolCalls).toContain('discover_agents({"group_id": "g1"})')
    expect(material.toolCalls).toContain('"group_id": "g1"')
  })

  it('keeps the whole-network MCP prompt unscoped', () => {
    const material = buildAccessMaterial('mcp', 'http://127.0.0.1:8001/mcp', '/api/v1')
    expect(material.prompt).toContain('discover_agents({})')
    expect(material.toolCalls).not.toContain('group_id')
  })

  it('scopes API discovery and the message body', () => {
    const material = buildAccessMaterial('api', 'https://hybro.example', '/api/v1', scope)
    expect(material.configuration).toContain('/api/v1/agents/discovery?group_id=g1')
    expect(material.toolCalls).toContain('/api/v1/agents/messages')
    expect(material.toolCalls).toContain('"group_id": "g1"')
    expect(material.prompt).toContain('group_id=g1')
  })
})
