'use client'

import { useId, useRef, useState, type FormEvent } from 'react'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { AgentPicker } from './agent-picker'
import type { NetworkFormDialogProps } from './types'

export function NetworkFormDialog({
  open,
  network,
  agents,
  agentsLoading,
  agentsError,
  pending,
  onOpenChange,
  onRetryAgents,
  onSave,
}: NetworkFormDialogProps) {
  const id = useId()
  const [name, setName] = useState(network?.name ?? '')
  const [description, setDescription] = useState(network?.description ?? '')
  const [agentIds, setAgentIds] = useState<string[]>([])
  const [nameError, setNameError] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const submitting = useRef(false)
  const nameInput = useRef<HTMLInputElement>(null)
  const busy = pending || saving

  function changeOpen(next: boolean) {
    if (pending || submitting.current) return
    onOpenChange(next)
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (pending || submitting.current || (!network && (agentsLoading || agentsError))) return
    const trimmedName = name.trim()
    if (!trimmedName) {
      setNameError(true)
      nameInput.current?.focus()
      return
    }
    submitting.current = true
    setSaving(true)
    setSaveError(null)
    try {
      await onSave({
        name: trimmedName,
        description: description.trim(),
        agentIds: network ? network.agents : agentIds,
      })
    } catch (error) {
      setSaveError(error instanceof Error && error.message ? error.message : 'Unable to save the network. Please try again.')
    } finally {
      submitting.current = false
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={changeOpen}>
      <DialogContent
        className="max-h-[calc(100dvh-2rem)] overflow-y-auto"
        showCloseButton={!busy}
        onEscapeKeyDown={event => { if (pending || submitting.current) event.preventDefault() }}
        onInteractOutside={event => { if (pending || submitting.current) event.preventDefault() }}
      >
        <DialogHeader>
          <DialogTitle>{network ? 'Edit network' : 'Create network'}</DialogTitle>
          <DialogDescription>
            {network ? 'Update this network’s name and description without changing its members.' : 'Name your network and choose agents, or start with an empty network.'}
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={save} className="flex flex-col gap-5" aria-busy={busy} noValidate>
          <fieldset className="flex min-w-0 flex-col gap-5" disabled={busy}>
            <legend className="sr-only">Network details</legend>
            <div className="flex flex-col gap-2" data-invalid={nameError || undefined}>
              <Label htmlFor={`${id}-name`}>Name</Label>
              <Input
                ref={nameInput}
                id={`${id}-name`}
                required
                value={name}
                onChange={event => {
                  setName(event.target.value)
                  setNameError(false)
                }}
                placeholder="e.g. Content creation"
                aria-invalid={nameError}
                aria-describedby={nameError ? `${id}-name-error` : undefined}
              />
              {nameError ? (
                <p id={`${id}-name-error`} role="alert" className="text-sm text-destructive">Enter a network name.</p>
              ) : null}
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor={`${id}-description`}>Description (optional)</Label>
              <Input
                id={`${id}-description`}
                value={description}
                onChange={event => setDescription(event.target.value)}
                placeholder="What can this network help you do?"
              />
            </div>
          </fieldset>
          {!network ? (
            <fieldset className="flex min-w-0 flex-col gap-3" disabled={busy}>
              <legend className="mb-3 text-sm font-medium">Agents (optional)</legend>
              <AgentPicker
                agents={agents}
                value={agentIds}
                onChange={setAgentIds}
                loading={agentsLoading}
                error={agentsError}
                disabled={busy}
                onRetry={onRetryAgents}
              />
            </fieldset>
          ) : null}
          {saveError ? <p role="alert" className="text-sm text-destructive wrap-anywhere">{saveError}</p> : null}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => changeOpen(false)} disabled={busy}>Cancel</Button>
            <Button type="submit" disabled={busy || (!network && (agentsLoading || Boolean(agentsError)))}>
              {busy ? 'Saving…' : network ? 'Save changes' : 'Create network'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
