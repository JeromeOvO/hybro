'use client'

import { useId, useRef, useState, type FormEvent } from 'react'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Field, FieldError, FieldLabel } from '@/components/ui/field'
import { AgentPicker } from '@/components/networks/agent-picker'
import type { SubnetDialogProps } from '@/components/networks/types'

export function SubnetDialog({
  open,
  network,
  initialAgentIds,
  agents,
  agentsLoading,
  agentsError,
  pending,
  onOpenChange,
  onRetryAgents,
  onSave,
  onDelete,
}: SubnetDialogProps) {
  const id = useId()
  const [name, setName] = useState(network?.name ?? '')
  const [description, setDescription] = useState(network?.description ?? '')
  const [agentIds, setAgentIds] = useState<string[]>(network?.agents ?? initialAgentIds ?? [])
  const [nameError, setNameError] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const submitting = useRef(false)
  const nameInput = useRef<HTMLInputElement>(null)
  const busy = pending || saving
  const membersChanged = !network || agentIds.length !== network.agents.length ||
    agentIds.some(agentId => !network.agents.includes(agentId))
  const membersBlocked = membersChanged && (agentsLoading || Boolean(agentsError))

  function changeOpen(next: boolean) {
    if (pending || submitting.current) return
    onOpenChange(next)
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (pending || submitting.current || membersBlocked) return
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
        agentIds,
      })
    } catch (error) {
      setSaveError(error instanceof Error && error.message ? error.message : "Couldn't save the subnet. Try again.")
    } finally {
      submitting.current = false
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={changeOpen}>
      <DialogContent
        className="flex max-h-[calc(100dvh-2rem)] flex-col gap-0 overflow-hidden p-0 sm:max-w-3xl"
        aria-describedby={undefined}
        showCloseButton={!busy}
        onEscapeKeyDown={event => { if (pending || submitting.current) event.preventDefault() }}
        onInteractOutside={event => { if (pending || submitting.current) event.preventDefault() }}
      >
        <form onSubmit={save} className="flex min-h-0 flex-1 flex-col" aria-busy={busy} noValidate>
          <DialogHeader className="border-b px-6 py-5 text-left">
            <DialogTitle>{network ? 'Edit subnet' : 'Create subnet'}</DialogTitle>
          </DialogHeader>
          <div className="grid min-h-0 flex-1 gap-6 overflow-y-auto px-6 py-5 md:grid-cols-[minmax(0,1fr)_minmax(0,1.25fr)] md:gap-8">
            <fieldset className="flex min-w-0 flex-col gap-5" disabled={busy}>
              <legend className="text-[15px] font-semibold">Details</legend>
              <Field data-invalid={nameError || undefined} data-disabled={busy}>
                <FieldLabel htmlFor={`${id}-name`}>Name</FieldLabel>
                <Input
                  ref={nameInput}
                  id={`${id}-name`}
                  required
                  disabled={busy}
                  value={name}
                  onChange={event => {
                    setName(event.target.value)
                    setNameError(false)
                  }}
                  placeholder="Content creation"
                  aria-invalid={nameError}
                  aria-describedby={nameError ? `${id}-name-error` : undefined}
                  className="h-9 rounded-lg"
                />
                {nameError ? (
                  <FieldError id={`${id}-name-error`}>Enter a subnet name.</FieldError>
                ) : null}
              </Field>
              <Field data-disabled={busy}>
                <FieldLabel htmlFor={`${id}-description`}>Description <span className="font-normal text-muted-foreground">(optional)</span></FieldLabel>
                <textarea
                  id={`${id}-description`}
                  disabled={busy}
                  value={description}
                  onChange={event => setDescription(event.target.value)}
                  placeholder="What can this subnet help with?"
                  rows={4}
                  className="min-h-24 w-full resize-y rounded-lg border border-input bg-transparent px-3 py-2 text-sm shadow-xs outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-input/30"
                />
              </Field>
              <p className="text-xs leading-relaxed text-muted-foreground">
                Clients can scope discovery and requests to this subnet by passing its ID. Scope is a per-request choice, not access control.
              </p>
            </fieldset>
            <section aria-labelledby={`${id}-members`} className="flex min-w-0 flex-col gap-3">
              <div className="flex items-baseline justify-between gap-3">
                <h3 id={`${id}-members`} className="text-[15px] font-semibold">Members</h3>
                <span className="text-xs text-muted-foreground">{agentIds.length === 1 ? '1 agent' : `${agentIds.length} agents`}</span>
              </div>
              <AgentPicker
                agents={agents}
                value={agentIds}
                onChange={setAgentIds}
                loading={agentsLoading}
                error={agentsError}
                disabled={busy}
                onRetry={onRetryAgents}
              />
            </section>
          </div>
          {saveError ? (
            <div className="px-6 pb-2">
              <Alert variant="destructive"><AlertDescription className="wrap-anywhere">{saveError}</AlertDescription></Alert>
            </div>
          ) : null}
          <DialogFooter className="border-t px-6 py-4">
            {onDelete ? (
              <Button type="button" variant="danger" className="sm:mr-auto" disabled={busy} onClick={onDelete}>
                Delete subnet
              </Button>
            ) : null}
            <Button type="button" variant="secondary" className="min-w-36" onClick={() => changeOpen(false)} disabled={busy}>Cancel</Button>
            <Button type="submit" className="min-w-36" disabled={busy || membersBlocked}>
              {busy ? 'Saving…' : network ? 'Save changes' : 'Create subnet'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
