'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { Loader2, Maximize, Minus, Network, Plus, RefreshCw, RotateCcw } from 'lucide-react'
import { AgentPicker } from '@/components/networks/agent-picker'
import { NetworkCanvas } from '@/components/networks/network-canvas'
import { NetworkDetailsPanel } from '@/components/networks/network-details-panel'
import { NetworkFormDialog } from '@/components/networks/network-form-dialog'
import { NetworkSidebar } from '@/components/networks/network-sidebar'
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
  AlertDialogTitle
} from '@/components/ui/alert-dialog'
import { banner } from '@/components/ui/banner'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { Separator } from '@/components/ui/separator'
import { useNetworks } from '@/hooks/useNetworks'
import { useAuth } from '@/lib/auth'
import type { AgentGroup } from '@/lib/types/agent-group'

type Confirmation = { type: 'delete'; networkId: string } | { type: 'remove'; networkId: string; agentId: string }

export default function NetworksPage() {
  const { isLoaded, isSignedIn } = useAuth()
  const data = useNetworks()
  const canvas = useRef<NetworkCanvasHandle>(null)
  const pendingFocus = useRef<string | null>(null)
  const [selection, setSelection] = useState<NetworkSelection | null>(null)
  const [zoom, setZoom] = useState(100)
  const [editor, setEditor] = useState<AgentGroup | 'create' | null>(null)
  const [memberTarget, setMemberTarget] = useState<string | null>(null)
  const [chosen, setChosen] = useState<string[]>([])
  const [memberError, setMemberError] = useState<string | null>(null)
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null)
  const [confirmationError, setConfirmationError] = useState<string | null>(null)
  const writeLock = useRef(false)

  const current =
    selection?.type === 'network' ? data.networks.find((network) => network.group_id === selection.id) : undefined
  const selectedAgent = selection?.type === 'agent' ? data.agents.find((agent) => agent.id === selection.id) : undefined
  const activeSelection = current || selectedAgent ? selection : null
  const detailsSelection: NetworkDetailsPanelProps['selection'] | null = current
    ? { type: 'network', network: current }
    : selectedAgent
      ? { type: 'agent', agent: selectedAgent }
      : null
  const memberNetwork = data.networks.find((network) => network.group_id === memberTarget)
  const confirmationNetwork = data.networks.find((network) => network.group_id === confirmation?.networkId)
  const busy = data.pending

  useEffect(() => {
    if (!pendingFocus.current) return
    canvas.current?.focusNetwork(pendingFocus.current)
    pendingFocus.current = null
  }, [selection, data.networks])

  const selectNetwork = useCallback((networkId: string) => {
    pendingFocus.current = networkId
    setSelection({ type: 'network', id: networkId })
  }, [])

  const openMembers = useCallback((networkId: string) => {
    setMemberTarget(networkId)
    setChosen([])
    setMemberError(null)
  }, [])

  async function addMembers() {
    if (!memberNetwork || chosen.length === 0 || writeLock.current) return
    writeLock.current = true
    setMemberError(null)
    try {
      const group = await data.setMembers(memberNetwork.group_id, [...new Set([...memberNetwork.agents, ...chosen])])
      setMemberTarget(null)
      selectNetwork(group.group_id)
      banner.success('Agents added to network')
    } catch (error) {
      setMemberError(error instanceof Error ? error.message : 'Unable to save changes. Please try again.')
    } finally {
      writeLock.current = false
    }
  }

  function askForConfirmation(next: Confirmation) {
    setConfirmationError(null)
    setConfirmation(next)
  }

  async function confirmChange() {
    if (!confirmation || writeLock.current) return
    if (!confirmationNetwork) {
      setConfirmationError('This network no longer exists. Close this dialog and refresh the list.')
      return
    }
    writeLock.current = true
    setConfirmationError(null)
    try {
      if (confirmation.type === 'delete') {
        await data.remove(confirmation.networkId)
        setSelection((previous) =>
          previous?.type === 'network' && previous.id === confirmation.networkId ? null : previous
        )
        banner.success('Network deleted. Agents preserved.')
      } else {
        await data.setMembers(
          confirmation.networkId,
          confirmationNetwork.agents.filter((id) => id !== confirmation.agentId)
        )
        banner.success('Member removed. Other networks are unchanged.')
      }
      setConfirmation(null)
    } catch (error) {
      setConfirmationError(error instanceof Error ? error.message : 'Unable to save changes. Please try again.')
    } finally {
      writeLock.current = false
    }
  }
  function overview() {
    setSelection(null)
    canvas.current?.fitAll()
  }

  if (isLoaded && !isSignedIn) {
    return (
      <section className="flex flex-1 flex-col items-center justify-center gap-4 p-8">
        <h1 className="text-xl font-semibold">Sign in to manage networks</h1>
        <Button asChild>
          <Link href="/sign-in">Sign in</Link>
        </Button>
      </section>
    )
  }

  return (
    <div className="networks-page">
      <h1 className="sr-only">Networks</h1>
      <NetworkSidebar
        networks={data.networks}
        selectedId={current?.group_id ?? null}
        loading={data.loading}
        refreshing={data.refreshing}
        error={data.error}
        disabled={busy || !isLoaded}
        onSelect={selectNetwork}
        onCreate={() => setEditor('create')}
        onRetry={() => {
          void data.refresh()
        }}
      />
      <div className="network-stage">
        <NetworkCanvas
          ref={canvas}
          networks={data.networks}
          agents={data.agents}
          selection={activeSelection}
          onSelect={setSelection}
          onZoomChange={setZoom}
        />
        {data.networks.length === 0 && data.agents.length === 0 ? (
          <div className="network-state" role={data.error || data.agentsError ? 'alert' : 'status'}>
            {data.loading || data.agentsLoading ? (
              <>
                <Loader2 className="size-6 animate-spin" aria-hidden="true" />
                <p>Loading agents and networks…</p>
              </>
            ) : data.error || data.agentsError ? (
              <>
                <h2 className="text-lg font-medium">Unable to load the overview</h2>
                <p>{data.error || data.agentsError}</p>
                <Button
                  variant="outline"
                  onClick={() => {
                    void data.refresh()
                  }}
                >
                  <RefreshCw data-icon="inline-start" />
                  Retry
                </Button>
              </>
            ) : (
              <>
                <Network className="size-9" aria-hidden="true" />
                <h2 className="text-lg font-medium">No agents or networks yet</h2>
                <p>Register or discover agents on the Agents page, or create a network.</p>
                <Button variant="outline" asChild>
                  <Link href="/agents">Open Agents</Link>
                </Button>
              </>
            )}
          </div>
        ) : null}
        {data.agentsError && (data.networks.length > 0 || data.agents.length > 0) ? (
          <div
            className="network-feedback flex flex-col gap-2 rounded-lg border bg-popover p-3 text-sm text-popover-foreground"
            role="alert"
          >
            <p>Some agent information could not be loaded. Available agents and saved memberships are preserved.</p>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                void data.refreshAgents()
              }}
            >
              <RefreshCw data-icon="inline-start" />
              Reload agents
            </Button>
          </div>
        ) : null}
        {detailsSelection ? (
          <NetworkDetailsPanel
            selection={detailsSelection}
            networks={data.networks}
            agents={data.agents}
            pending={busy}
            onClose={() => setSelection(null)}
            onEdit={() => {
              if (current) setEditor(current)
            }}
            onAddAgents={() => {
              if (current) openMembers(current.group_id)
            }}
            onDelete={() => {
              if (current) askForConfirmation({ type: 'delete', networkId: current.group_id })
            }}
            onRemoveAgent={(networkId, agentId) => askForConfirmation({ type: 'remove', networkId, agentId })}
            onSelectNetwork={selectNetwork}
            onSelectAgent={(id) => setSelection({ type: 'agent', id })}
          />
        ) : null}
        <div className="network-controls" aria-label="Canvas view controls">
          <Button variant="ghost" size="icon" aria-label="Zoom out" onClick={() => canvas.current?.zoom(0.8)}>
            <Minus />
          </Button>
          <output className="network-zoom-label text-xs" aria-label="Current zoom">
            {zoom}%
          </output>
          <Button variant="ghost" size="icon" aria-label="Zoom in" onClick={() => canvas.current?.zoom(1.25)}>
            <Plus />
          </Button>
          <Separator orientation="vertical" className="mx-1 min-h-5" />
          <Button variant="ghost" size="icon" aria-label="Show all connections" onClick={overview}>
            <Maximize />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            aria-label="Reset node layout"
            onClick={() => canvas.current?.resetLayout()}
          >
            <RotateCcw />
          </Button>
        </div>
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
          onOpenChange={(open) => {
            if (!open && !busy) setEditor(null)
          }}
          onRetryAgents={() => {
            void data.refreshAgents()
          }}
          onSave={async (values) => {
            const saved =
              editor === 'create'
                ? await data.create(values)
                : await data.update(editor.group_id, { name: values.name, description: values.description })
            setEditor(null)
            selectNetwork(saved.group_id)
            banner.success(editor === 'create' ? 'Network created' : 'Network updated')
          }}
        />
      ) : null}

      <Dialog
        open={memberTarget !== null}
        onOpenChange={(open) => {
          if (!open && !busy) setMemberTarget(null)
        }}
      >
        <DialogContent showCloseButton={!busy}>
          <DialogHeader>
            <DialogTitle>Add agents</DialogTitle>
            <DialogDescription>
              {memberNetwork
                ? `Add members to “${memberNetwork.name}” without duplicating agent instances.`
                : 'This network no longer exists.'}
            </DialogDescription>
          </DialogHeader>
          {memberTarget ? (
            <AgentPicker
              key={memberTarget}
              agents={data.agents}
              value={chosen}
              onChange={setChosen}
              existingIds={memberNetwork?.agents}
              loading={data.agentsLoading}
              error={data.agentsError}
              disabled={busy || !memberNetwork}
              onRetry={() => {
                void data.refreshAgents()
              }}
            />
          ) : null}
          {memberError ? (
            <p className="text-sm text-destructive" role="alert">
              {memberError}
            </p>
          ) : null}
          <DialogFooter>
            <Button variant="outline" disabled={busy} onClick={() => setMemberTarget(null)}>
              Cancel
            </Button>
            <Button
              disabled={
                busy || !memberNetwork || chosen.length === 0 || data.agentsLoading || Boolean(data.agentsError)
              }
              onClick={() => {
                void addMembers()
              }}
            >
              {busy ? <Loader2 className="animate-spin" data-icon="inline-start" /> : <Plus data-icon="inline-start" />}
              {busy
                ? 'Saving…'
                : chosen.length
                  ? `Add ${chosen.length} ${chosen.length === 1 ? 'agent' : 'agents'}`
                  : 'Add agents'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog
        open={confirmation !== null}
        onOpenChange={(open) => {
          if (!open && !busy) setConfirmation(null)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {confirmation?.type === 'delete' ? 'Delete this network?' : 'Remove this member?'}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {confirmation?.type === 'delete'
                ? `Delete “${confirmationNetwork?.name ?? 'Network'}” and its memberships. Agents and existing rooms will not be deleted.`
                : `Remove this member from “${confirmationNetwork?.name ?? 'Network'}”. The agent and its memberships in other networks will be preserved.`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          {confirmationError ? (
            <p className="text-sm text-destructive" role="alert">
              {confirmationError}
            </p>
          ) : null}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
            <AlertDialogAction asChild>
              <Button
                variant="destructive"
                disabled={busy}
                onClick={(event) => {
                  event.preventDefault()
                  void confirmChange()
                }}
              >
                {busy ? <Loader2 className="animate-spin" data-icon="inline-start" /> : null}
                {busy ? 'Saving…' : confirmation?.type === 'delete' ? 'Delete network' : 'Remove member'}
              </Button>
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
