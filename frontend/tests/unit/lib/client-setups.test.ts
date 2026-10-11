import { describe, expect, it } from 'vitest'
import { clientSetups } from '@/components/connect/client-setups'
import { buildMcpConfig } from '@/lib/mcp'

const url = 'http://127.0.0.1:8001/mcp'

describe('clientSetups', () => {
  it('gives every supported assistant a snippet with the address', () => {
    const setups = clientSetups(url)
    expect(setups.map(setup => setup.label)).toEqual(['Claude Code', 'Cursor', 'Codex', 'VS Code', 'Other'])
    for (const setup of setups) expect(setup.snippet).toContain(url)
  })

  it('uses the standard configuration for other clients and a command for Claude Code', () => {
    const setups = clientSetups(url)
    expect(setups.find(setup => setup.id === 'other')?.snippet).toBe(buildMcpConfig(url))
    expect(setups.find(setup => setup.id === 'claude-code')).toMatchObject({ kind: 'command', snippet: `claude mcp add --transport http hybro ${url}` })
    expect(setups.find(setup => setup.id === 'codex')?.snippet).toBe(`[mcp_servers.hybro]\nurl = "${url}"`)
  })
})
