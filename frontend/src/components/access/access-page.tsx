'use client'

import { useRef, useState } from 'react'
import { ChevronDown, Copy } from 'lucide-react'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { publicConfig } from '@/lib/config'
import { MCP_URL } from '@/lib/mcp'
import { API_AUTH_HELP, MCP_AUTH_HELP, buildAccessMaterial, type AccessKind } from './access-material'
import { McpStatusControl } from './mcp-status'

export function AccessPage({ kind }: { kind: AccessKind }) {
  const mcp = kind === 'mcp'
  const config = publicConfig()
  const url = mcp ? MCP_URL : config.api_base_url
  const [tab, setTab] = useState('config')
  const [copyFeedback, setCopyFeedback] = useState<{ value: string; message: string; failed: boolean } | null>(null)
  const [copying, setCopying] = useState(false)
  const codeRef = useRef<HTMLPreElement>(null)
  const material = buildAccessMaterial(kind, url, config.api_prefix)
  const text = tab === 'prompt' ? material.prompt : material.configuration
  const feedback = copyFeedback?.value === text || copyFeedback?.value === url ? copyFeedback : null

  async function copy(value: string, label: string) {
    if (copying) return
    setCopying(true)
    try {
      await navigator.clipboard.writeText(value)
      setCopyFeedback({ value, message: `${label} copied`, failed: false })
    } catch {
      setCopyFeedback({ value, message: 'Unable to copy. Select and copy the text manually.', failed: true })
      if (value === text && codeRef.current) {
        codeRef.current.focus()
        const range = document.createRange()
        range.selectNodeContents(codeRef.current)
        const selection = window.getSelection()
        selection?.removeAllRanges()
        selection?.addRange(range)
      }
    } finally {
      setCopying(false)
    }
  }

  return (
    <section className="page-container" aria-labelledby="access-heading">
      <div className="page-content flex min-w-0 flex-col gap-6">
        <h1 id="access-heading" className="text-2xl font-bold">{mcp ? 'MCP' : 'API'}</h1>

        <Tabs value={tab} onValueChange={setTab} className="min-w-0 gap-4">
          <div className="flex min-w-0 flex-wrap items-center justify-between gap-3">
            <TabsList aria-label="Access materials" className="max-w-full flex-wrap justify-start group-data-[orientation=horizontal]/tabs:h-auto">
              <TabsTrigger value="config" className="min-h-9 flex-none whitespace-normal sm:whitespace-nowrap">{mcp ? 'Connection configuration' : 'Request example'}</TabsTrigger>
              <TabsTrigger value="prompt" className="min-h-9 flex-none whitespace-normal sm:whitespace-nowrap">AI setup prompt</TabsTrigger>
            </TabsList>
            <div className="flex flex-wrap gap-2">
              {mcp ? <Button variant="outline" disabled={copying} onClick={() => { void copy(url, 'URL') }}>Copy URL</Button> : null}
              <Button disabled={copying} onClick={() => { void copy(text, tab === 'prompt' ? 'Prompt' : mcp ? 'Configuration' : 'Request') }}>
                <Copy data-icon="inline-start" aria-hidden="true" />
                {tab === 'prompt' ? 'Copy prompt' : mcp ? 'Copy configuration' : 'Copy request'}
              </Button>
            </div>
          </div>
          {['config', 'prompt'].map(panel => (
            <TabsContent key={panel} value={panel} className="min-w-0">
              <pre ref={panel === tab ? codeRef : undefined} tabIndex={0} aria-label="Access material" className="min-w-0 select-text overflow-x-auto rounded-md bg-muted p-4 text-xs leading-relaxed whitespace-pre-wrap wrap-anywhere">
                {panel === 'prompt' ? material.prompt : material.configuration}
              </pre>
            </TabsContent>
          ))}
        </Tabs>
        {feedback ? (
          feedback.failed ? <Alert variant="destructive"><AlertDescription>{feedback.message}</AlertDescription></Alert>
            : <p role="status" className="text-sm text-muted-foreground">{feedback.message}</p>
        ) : null}
        {mcp ? <McpStatusControl /> : null}

        <Collapsible className="group">
          <CollapsibleTrigger asChild>
            <Button variant="ghost" size="sm">
              <ChevronDown data-icon="inline-start" className="group-data-[state=open]:rotate-180" aria-hidden="true" />
              Connection details
            </Button>
          </CollapsibleTrigger>
          <CollapsibleContent>
            <div className="flex min-w-0 flex-col gap-3 pt-3 text-sm leading-relaxed text-muted-foreground wrap-anywhere">
              <pre className="rounded-md bg-muted p-3 text-xs whitespace-pre-wrap wrap-anywhere">{material.scopeCall}</pre>
              <p>{mcp ? MCP_AUTH_HELP : API_AUTH_HELP}</p>
              <p>Copying does not connect a client or execute a request.</p>
            </div>
          </CollapsibleContent>
        </Collapsible>
      </div>
    </section>
  )
}
