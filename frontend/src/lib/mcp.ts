export const MCP_URL = 'http://127.0.0.1:8001/mcp'
export function buildMcpConfig(url: string): string {
  return JSON.stringify({
    mcpServers: { hybro: { type: 'http', url } }
  }, null, 2)
}

export const MCP_CONFIG = buildMcpConfig(MCP_URL)

export type McpStatus = 'ready' | 'unavailable' | 'unsupported_auth'

export function parseMcpStatus(value: unknown): McpStatus {
  if (typeof value !== 'object' || value === null || !('service' in value) || value.service !== 'hybro-mcp') {
    return 'unavailable'
  }
  if ('status' in value && (value.status === 'ready' || value.status === 'unsupported_auth')) {
    return value.status
  }
  return 'unavailable'
}
