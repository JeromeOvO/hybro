import { publicConfig } from '@/lib/config'

/** This instance's MCP address; the CLI derives it from the instance's host port. */
export function mcpUrl(): string {
  return publicConfig().mcp_url
}

export function buildMcpConfig(url: string): string {
  return JSON.stringify({
    mcpServers: { hybro: { type: 'http', url } }
  }, null, 2)
}

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
