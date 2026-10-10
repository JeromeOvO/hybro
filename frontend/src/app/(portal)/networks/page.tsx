'use client'

import { useCallback, useRef, useState } from 'react'
import Link from 'next/link'
import { Loader2, Maximize, Minus, Network, Plus, RefreshCw, RotateCcw } from 'lucide-react'
import { NetworkCanvas } from '@/components/networks/network-canvas'
import { NetworkDetailsPanel } from '@/components/networks/network-details-panel'
import { NetworkFormDialog } from '@/components/networks/network-form-dialog'
import type { NetworkCanvasHandle, NetworkDetailsPanelProps, NetworkSelection } from '@/components/networks/types'
import '@/components/networks/networks.css'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { banner } from '@/components/ui/banner'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { Separator } from '@/components/ui/separator'
import { useNetworks } from '@/hooks/useNetworks'
import { useAuth } from '@/lib/auth'
import type { AgentGroup } from '@/lib/types/agent-group'

export default function NetworksPage() {
  const { isLoaded, isSignedIn } = useAuth()
  const data = useNetworks()
  const stage = useRef<HTMLDivElement>(null)
  const canvas = useRef<NetworkCanvasHandle>(null)
  const [selection, setSelection] = useState<NetworkSelection | null>(null)
  const [scopeId, setScopeId] = useState<string | null>(null)
  const [zoom, setZoom] = useState(141)
  const [editor, setEditor] = useState<AgentGroup | 'create' | null>(null)
  const [deleting, setDeleting] = useState<AgentGroup | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const writeLock = useRef(false)
  const current = selection?.type === 'network'
    ? data.networks.find(network => network.group_id === selection.id)
    : undefined
  const selectedAgent = selection?.type === 'agent'
    ? data.agents.find(agent => agent.id === selection.id)
    : undefined
  const scope = data.networks.find(network => network.group_id === scopeId)
  const details: NetworkDetailsPanelProps['selection'] | null = current
    ? { type: 'network', network: current }
    : selectedAgent ? { type: 'agent', agent: selectedAgent } : null
  const busy = data.pending

  const select = useCallback((next: NetworkSelection | null) => {
    setSelection(next)
    if (next?.type === 'network') setScopeId(next.id)
  }, [])

  function closeDetails() {
    const node = selection && stage.current?.querySelector<SVGGElement>(
      `[data-node-key="${CSS.escape(`${selection.type}:${selection.id}`)}"]`,
    )
    setSelection(null)
    node?.focus({ preventScroll: true })
  }

  function overview() {
    setSelection(null)
    setScopeId(null)
    canvas.current?.fitAll()
  }

  async function deleteSubnet() {
    if (!deleting || writeLock.current) return
    writeLock.current = true
    setDeleteError(null)
    try {
      await data.remove(deleting.group_id)
      setSelection(null)
      setScopeId(previous => previous === deleting.group_id ? null : previous)
      setDeleting(null)
      banner.success('Subnet deleted. Agents and rooms preserved.')
      stage.current?.querySelector<SVGSVGElement>('.network-graph')?.focus()
    } catch (error) {
      setDeleteError(error instanceof Error ? error.message : 'Unable to delete this subnet. Please try again.')
    } finally {
      writeLock.current = false
    }
  }

  if (isLoaded && !isSignedIn) {
    return (
      <section className="flex flex-1 flex-col items-center justify-center gap-4 p-8">
        <h1 className="text-xl font-semibold">Sign in to manage subnets</h1>
        <Button asChild><Link href="/sign-in">Sign in</Link></Button>
      </section>
    )
  }

  return (
    <div className="networks-page">
      <h1 className="sr-only">Subnets</h1>
      <div className="network-stage" ref={stage}>
        <NetworkCanvas
          ref={canvas}
          networks={data.networks}
          agents={data.agents}
          selection={scope ? { type: 'network', id: scope.group_id } : null}
          onSelect={select}
          onZoomChange={setZoom}
        />
        <Button className="network-create" disabled={busy || !isLoaded} onClick={() => setEditor('create')}>
          <Plus data-icon="inline-start" />
          New subnet
        </Button>
        {data.networks.length === 0 && data.agents.length === 0 ? (
          <div className="network-state">
            {data.loading || data.agentsLoading ? (
              <Card role="status" className="items-center px-6">
                <Loader2 className="size-6 animate-spin" aria-hidden="true" />
                <p>Loading agents and subnets…</p>
              </Card>
            ) : data.error || data.agentsError ? (
              <Alert variant="destructive">
                <AlertTitle role="heading" aria-level={2}>Unable to load the overview</AlertTitle>
                <AlertDescription className="gap-4">
                  <p>{data.error || data.agentsError}</p>
                  <Button variant="outline" onClick={() => { void data.refresh() }}>
                    <RefreshCw data-icon="inline-start" />Retry
                  </Button>
                </AlertDescription>
              </Alert>
            ) : (
              <Card role="status" className="py-0">
                <Empty>
                  <EmptyHeader>
                    <EmptyMedia variant="icon"><Network aria-hidden="true" /></EmptyMedia>
                    <EmptyTitle role="heading" aria-level={2}>No agents or subnets yet</EmptyTitle>
                    <EmptyDescription>Register or discover agents, then group them into a subnet.</EmptyDescription>
                  </EmptyHeader>
                  <EmptyContent>
                    <Button variant="outline" asChild><Link href="/agents">Open Agents</Link></Button>
                  </EmptyContent>
                </Empty>
              </Card>
            )}
          </div>
        ) : null}
        {(data.error || data.agentsError) && (data.networks.length > 0 || data.agents.length > 0) ? (
          <Alert className="network-feedback" variant="destructive">
            <AlertDescription className="gap-2">
              <p>{data.error || 'Some agent information could not be loaded. Saved memberships are preserved.'}</p>
              <Button variant="outline" size="sm" onClick={() => { void data.refresh() }}>
                <RefreshCw data-icon="inline-start" />Retry
              </Button>
            </AlertDescription>
          </Alert>
        ) : null}
        {details ? (
          <NetworkDetailsPanel
            selection={details}
            scope={scope}
            agents={data.agents}
            pending={busy}
            onClose={closeDetails}
            onManage={() => { if (current) setEditor(current) }}
          />
        ) : null}
        <Card className="network-controls flex-row items-center gap-0.5 p-1" role="group" aria-label="Canvas view controls">
          <Button variant="ghost" size="icon" aria-label="Zoom out" onClick={() => canvas.current?.zoom(0.8)}><Minus /></Button>
          <output className="network-zoom-label text-xs" aria-label="Current zoom">{zoom}%</output>
          <Button variant="ghost" size="icon" aria-label="Zoom in" onClick={() => canvas.current?.zoom(1.25)}><Plus /></Button>
          <Separator orientation="vertical" className="mx-1 min-h-5" />
          <Button variant="ghost" size="icon" aria-label="Show all connections" onClick={overview}><Maximize /></Button>
          <Button variant="ghost" size="icon" aria-label="Reset node layout" onClick={() => canvas.current?.resetLayout()}><RotateCcw /></Button>
        </Card>
      </div>
      {editor ? (
        <NetworkFormDialog
          key={editor === 'create' ? 'create' : editor.group_id}
          open
          network={editor === 'create' ? undefined : editor}
          agents={data.agents}
          agentsLoading={data.agentsLoading}
          agentsError={data.agentsError}
          pending={busy}
          onOpenChange={open => { if (!open && !busy) setEditor(null) }}
          onRetryAgents={() => { void data.refreshAgents() }}
          onDelete={editor === 'create' ? undefined : () => {
            setDeleting(editor)
            setDeleteError(null)
            setEditor(null)
          }}
          onSave={async values => {
            // Leave memberships out of metadata-only edits to preserve concurrent changes.
            const membersChanged = editor !== 'create' && (
              values.agentIds.length !== editor.agents.length ||
              values.agentIds.some(id => !editor.agents.includes(id))
            )
            const saved = editor === 'create'
              ? await data.create(values)
              : await data.update(editor.group_id, {
                name: values.name,
                description: values.description,
                ...(membersChanged ? { agents: values.agentIds } : {}),
              })
            setEditor(null)
            select({ type: 'network', id: saved.group_id })
            banner.success(editor === 'create' ? 'Subnet created' : 'Subnet updated')
          }}
        />
      ) : null}
      <AlertDialog open={deleting !== null} onOpenChange={open => { if (!open && !busy) setDeleting(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this subnet?</AlertDialogTitle>
            <AlertDialogDescription>
              Delete “{deleting?.name}” and its memberships. Agents, other subnets and existing rooms will not be deleted.
            </AlertDialogDescription>
          </AlertDialogHeader>
          {deleteError ? <Alert variant="destructive"><AlertDescription>{deleteError}</AlertDescription></Alert> : null}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
            <AlertDialogAction asChild>
              <Button variant="destructive" disabled={busy} onClick={event => { event.preventDefault(); void deleteSubnet() }}>
                {busy ? <Loader2 className="animate-spin" data-icon="inline-start" /> : null}
                {busy ? 'Deleting…' : 'Delete subnet'}
              </Button>
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
