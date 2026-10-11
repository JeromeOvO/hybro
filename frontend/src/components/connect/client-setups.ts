import { buildMcpConfig } from '@/lib/mcp'

export interface ClientSetup {
  id: string
  label: string
  /** The text the user runs or pastes. */
  snippet: string
  kind: 'command' | 'configuration'
  /** Where the snippet goes. */
  hint: string
}

export function clientSetups(url: string): ClientSetup[] {
  return [
    { id: 'claude-code', label: 'Claude Code', snippet: `claude mcp add --transport http hybro ${url}`, kind: 'command', hint: 'Run in a terminal' },
    { id: 'cursor', label: 'Cursor', snippet: JSON.stringify({ mcpServers: { hybro: { url } } }, null, 2), kind: 'configuration', hint: 'Add to .cursor/mcp.json' },
    { id: 'codex', label: 'Codex', snippet: `[mcp_servers.hybro]\nurl = "${url}"`, kind: 'configuration', hint: 'Add to ~/.codex/config.toml' },
    { id: 'vscode', label: 'VS Code', snippet: JSON.stringify({ servers: { hybro: { type: 'http', url } } }, null, 2), kind: 'configuration', hint: 'Add to .vscode/mcp.json' },
    { id: 'other', label: 'Other', snippet: buildMcpConfig(url), kind: 'configuration', hint: 'Standard mcpServers configuration' },
  ]
}
