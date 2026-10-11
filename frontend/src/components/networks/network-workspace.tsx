'use client'

import { useCallback, useMemo, useRef, useState } from 'react'
import { Loader2, Maximize, Minus, Network as NetworkIcon, Plus, RefreshCw, RotateCcw } from 'lucide-react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { NetworkCanvas } from './network-canvas'
import { NetworkDetailsPanel } from './network-details-panel'
import { NetworkToolbar } from './network-toolbar'
import { StructureView } from './structure-view'
import { GraphDisplayMenu } from './graph-display-menu'
import type { NetworkCanvasHandle, NetworkSelection } from './types'
import './networks.css'
import { PageHeader } from '@/components/page-header'
import { SubnetDialog } from '@/components/subnets/subnet-dialog'
import { SubnetDeleteDialog } from '@/components/subnets/subnet-delete-dialog'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { Separator } from '@/components/ui/separator'
import { useNetworks } from '@/hooks/useNetworks'
import { useNetworkUrlState } from '@/hooks/useNetworkUrlState'
import { useGraphDisplayPrefs } from '@/hooks/useGraphDisplayPrefs'
import { useAuth } from '@/lib/auth'
import { discoverLocalAgents } from '@/lib/api/agent'
import { resolveNetworkUrlState } from '@/lib/network-url-state'
import { searchNodeKeys } from '@/lib/network-search'
import type { AgentGroup } from '@/lib/types/agent-group'

export function NetworkWorkspace() {
  const { getToken } = useAuth()
  const queryClient = useQueryClient()
  const data = useNetworks()
  const [rawState, setState] = useNetworkUrlState()
  const [display, setDisplay] = useGraphDisplayPrefs()
  const canvas = useRef<NetworkCanvasHandle>(null)
  const [zoom, setZoom] = useState(141)
  const [editor, setEditor] = useState<{ network?: AgentGroup; agentIds?: string[] } | null>(null)
  const [deleting, setDeleting] = useState<AgentGroup | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const loaded = !data.loading && !data.agentsLoading
  const state = useMemo(() => loaded
    ? resolveNetworkUrlState(rawState, {
      subnetIds: new Set(data.networks.map(network => network.group_id)),
      agentIds: new Set(data.agents.map(agent => agent.id)),
    })
    : rawState, [loaded, rawState, data.networks, data.agents])
  const search = useMemo(() => searchNodeKeys(state.q, data.agents, data.networks), [state.q, data.agents, data.networks])

  const discovery = useMutation({
    mutationFn: () => discoverLocalAgents(getToken),
    onSuccess: async result => {
      await queryClient.invalidateQueries({ queryKey: ['networks'] })
      await data.refreshAgents()
      toast.success(result.agents_found === 1 ? 'Found 1 local agent' : `Found ${result.agents_found} local agents`)
    },
    onError: error => toast.error("Couldn't discover local agents", { description: error instanceof Error ? error.message : undefined }),
  })

  const selected = state.select
  const selectedNetwork = selected?.type === 'subnet' ? data.networks.find(n => n.group_id === selected.id) : undefined
  const selectedAgent = selected?.type === 'agent' ? data.agents.find(a => a.id === selected.id) : undefined
  const canvasSelection: NetworkSelection | null = selected
    ? { type: selected.type === 'subnet' ? 'network' : 'agent', id: selected.id }
    : null
  const onCanvasSelect = useCallback((next: NetworkSelection | null) => {
    setState({ select: next ? { type: next.type === 'network' ? 'subnet' : 'agent', id: next.id } : null })
  }, [setState])

  const details = selectedNetwork || selectedAgent ? (
    <NetworkDetailsPanel
      selection={selectedNetwork ? { type: 'network', network: selectedNetwork } : { type: 'agent', agent: selectedAgent! }}
      networks={data.networks}
      agents={data.agents}
      pending={data.pending}
      onClose={() => setState({ select: null })}
      onManage={() => selectedNetwork && setEditor({ network: selectedNetwork })}
      onDelete={() => { if (selectedNetwork) { setDeleting(selectedNetwork); setDeleteError(null) } }}
      onSetMembers={data.setMembers}
      onCreateWithAgent={agentId => setEditor({ agentIds: [agentId] })}
    />
  ) : null

  async function confirmDelete() {
    if (!deleting) return
    setDeleteError(null)
    try {
      await data.remove(deleting.group_id)
      setState({ select: null, ...(state.scope.kind === 'subnet' && state.scope.id === deleting.group_id ? { scope: { kind: 'all' } } : {}) })
      setDeleting(null)
      toast.success('Subnet deleted. Agents and rooms are kept.')
    } catch (error) {
      setDeleteError(error instanceof Error ? error.message : "Couldn't delete this subnet. Try again.")
    }
  }

  const empty = loaded && data.networks.length === 0 && data.agents.length === 0
  const failed = data.error || data.agentsError

  return (
    <div className="networks-page">
      <div className="flex flex-col gap-4 px-6 pt-6 pb-4 max-md:px-3">
        <PageHeader title="Network" />
        <NetworkToolbar
          view={state.view}
          q={state.q}
          discovering={discovery.isPending}
          onViewChange={view => setState({ view })}
          onSearch={q => setState({ q })}
          onDiscover={() => discovery.mutate()}
          onCreateSubnet={() => setEditor({})}
        />
        {failed && !empty ? (
          <Alert variant="destructive">
            <AlertDescription className="flex flex-wrap items-center gap-2">
              <span>{data.error || "Some agent details couldn't load. Saved memberships are kept."}</span>
              <Button variant="secondary" size="sm" onClick={() => { void data.refresh() }}><RefreshCw data-icon="inline-start" aria-hidden="true" />Retry</Button>
            </AlertDescription>
          </Alert>
        ) : null}
      </div>
      {!loaded ? (
        <div role="status" className="flex flex-1 items-center justify-center gap-3 text-sm text-muted-foreground">
          <Loader2 className="size-5 motion-safe:animate-spin" aria-hidden="true" />Loading agents and subnets…
        </div>
      ) : empty ? (
        <div className="flex flex-1 items-center justify-center p-6">
          {failed ? (
            <Alert variant="destructive" className="max-w-md">
              <AlertTitle role="heading" aria-level={2}>Couldn&apos;t load the network</AlertTitle>
              <AlertDescription className="gap-3">
                <p>{failed}</p>
                <Button variant="secondary" onClick={() => { void data.refresh() }}><RefreshCw data-icon="inline-start" aria-hidden="true" />Retry</Button>
              </AlertDescription>
            </Alert>
          ) : (
            <Empty>
              <EmptyHeader>
                <EmptyMedia variant="icon"><NetworkIcon aria-hidden="true" /></EmptyMedia>
                <EmptyTitle role="heading" aria-level={2}>Add your first agent</EmptyTitle>
                <EmptyDescription>Register an agent by URL or discover agents running on this machine, then group them into subnets.</EmptyDescription>
              </EmptyHeader>
              <EmptyContent>
                <Button variant="secondary" onClick={() => discovery.mutate()}>Discover local agents</Button>
              </EmptyContent>
            </Empty>
          )}
        </div>
      ) : state.view === 'structure' ? (
        <StructureView agents={data.agents} networks={data.networks} state={state} onChange={setState} details={details} />
      ) : (
        <div className="network-topology">
          <div className="network-stage">
            <NetworkCanvas
              ref={canvas}
              networks={data.networks}
              agents={data.agents}
              selection={canvasSelection}
              search={search}
              display={display}
              onSelect={onCanvasSelect}
              onZoomChange={setZoom}
            />
            <Card className="network-controls flex-row items-center gap-0.5 p-1" role="group" aria-label="Canvas view controls">
              <Button variant="ghost" size="icon" aria-label="Zoom out" onClick={() => canvas.current?.zoom(0.8)}><Minus /></Button>
              <output className="network-zoom-label text-xs" aria-label="Current zoom">{zoom}%</output>
              <Button variant="ghost" size="icon" aria-label="Zoom in" onClick={() => canvas.current?.zoom(1.25)}><Plus /></Button>
              <Separator orientation="vertical" className="mx-1 min-h-5" />
              <Button variant="ghost" size="icon" aria-label="Fit all" onClick={() => canvas.current?.fitAll()}><Maximize /></Button>
              <Button variant="ghost" size="icon" aria-label="Reset node layout" onClick={() => canvas.current?.resetLayout()}><RotateCcw /></Button>
              <GraphDisplayMenu value={display} onChange={setDisplay} />
            </Card>
          </div>
          {details}
        </div>
      )}
      {editor ? (
        <SubnetDialog
          key={editor.network?.group_id ?? 'create'}
          open
          network={editor.network}
          initialAgentIds={editor.agentIds}
          agents={data.agents}
          agentsLoading={data.agentsLoading}
          agentsError={data.agentsError}
          pending={data.pending}
          onOpenChange={open => { if (!open && !data.pending) setEditor(null) }}
          onRetryAgents={() => { void data.refreshAgents() }}
          onDelete={editor.network ? () => { setDeleting(editor.network!); setDeleteError(null); setEditor(null) } : undefined}
          onSave={async values => {
            const network = editor.network
            // Leave memberships out of metadata-only edits to preserve concurrent changes.
            const membersChanged = network !== undefined && (
              values.agentIds.length !== network.agents.length || values.agentIds.some(id => !network.agents.includes(id)))
            const saved = network
              ? await data.update(network.group_id, { name: values.name, description: values.description, ...(membersChanged ? { agents: values.agentIds } : {}) })
              : await data.create(values)
            setEditor(null)
            setState({ select: { type: 'subnet', id: saved.group_id } })
            toast.success(network ? 'Subnet updated' : 'Subnet created')
          }}
        />
      ) : null}
      <SubnetDeleteDialog
        subnet={deleting}
        pending={data.pending}
        error={deleteError}
        onCancel={() => setDeleting(null)}
        onConfirm={() => { void confirmDelete() }}
      />
    </div>
  )
}
