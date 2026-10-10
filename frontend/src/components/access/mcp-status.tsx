'use client'

import { useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { parseMcpStatus, type McpStatus } from '@/lib/mcp'

const statusLabels = {
  checking: 'Checking…',
  ready: 'Ready',
  unavailable: 'Unavailable',
  unsupported_auth: 'Unavailable with Clerk',
  error: 'Unable to check status'
}

export function McpStatusControl() {
  const [status, setStatus] = useState<McpStatus | 'checking' | 'error'>('checking')
  const [refresh, setRefresh] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    const timeout = setTimeout(() => {
      setStatus('error')
      controller.abort()
    }, 6000)
    async function checkStatus() {
      try {
        const response = await fetch('/hybro-mcp', { cache: 'no-store', signal: controller.signal })
        if (!response.ok) throw new Error('Status unavailable')
        const body: unknown = await response.json()
        if (!controller.signal.aborted) setStatus(parseMcpStatus(body))
      } catch {
        if (!controller.signal.aborted) setStatus('error')
      } finally {
        clearTimeout(timeout)
      }
    }
    void checkStatus()
    return () => {
      clearTimeout(timeout)
      controller.abort()
    }
  }, [refresh])

  return (
    <div className="flex flex-col gap-2 text-sm text-muted-foreground">
      <div className="flex flex-wrap items-center gap-2">
        <span>Bundled local adapter:</span>
        <Badge role="status" variant={status === 'ready' ? 'outline' : 'secondary'}>{statusLabels[status]}</Badge>
        <Button variant="ghost" size="icon" className="size-11" aria-label="Refresh MCP status" disabled={status === 'checking'} onClick={() => {
          setStatus('checking')
          setRefresh(value => value + 1)
        }}>
          <RefreshCw aria-hidden="true" />
        </Button>
      </div>
      {status === 'unavailable' ? <p>Start services or check MCP logs in the Hybro TUI.</p> : null}
      {status === 'unsupported_auth' ? <p>This local adapter does not support Clerk authentication.</p> : null}
    </div>
  )
}
