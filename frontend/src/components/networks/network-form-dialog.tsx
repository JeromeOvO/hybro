'use client'

import { useId, useRef, useState, type FormEvent } from 'react'
import { Alert, AlertDescription } from '@/components/ui/alert'
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
import { Field, FieldError, FieldGroup, FieldLabel, FieldLegend, FieldSet } from '@/components/ui/field'
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
  onDelete,
}: NetworkFormDialogProps) {
  const id = useId()
  const [name, setName] = useState(network?.name ?? '')
  const [description, setDescription] = useState(network?.description ?? '')
  const [agentIds, setAgentIds] = useState<string[]>(network?.agents ?? [])
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
          <DialogTitle>{network ? 'Manage subnet' : 'New subnet'}</DialogTitle>
          <DialogDescription>
            Membership changes only affect this discovery scope. Agents can belong to more than one subnet.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={save} className="flex flex-col gap-5" aria-busy={busy} noValidate>
          <FieldGroup>
            <FieldSet className="min-w-0" disabled={busy}>
              <FieldLegend className="sr-only">Network details</FieldLegend>
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
                  placeholder="e.g. Content creation"
                  aria-invalid={nameError}
                  aria-describedby={nameError ? `${id}-name-error` : undefined}
                />
                {nameError ? (
                  <FieldError id={`${id}-name-error`}>Enter a network name.</FieldError>
                ) : null}
              </Field>
              <Field data-disabled={busy}>
                <FieldLabel htmlFor={`${id}-description`}>Description (optional)</FieldLabel>
                <Input
                  id={`${id}-description`}
                  disabled={busy}
                  value={description}
                  onChange={event => setDescription(event.target.value)}
                  placeholder="What can this network help you do?"
                />
              </Field>
            </FieldSet>
            <FieldSet className="min-w-0 gap-3" disabled={busy}>
              <FieldLegend variant="label">Members</FieldLegend>
              <AgentPicker
                agents={agents}
                value={agentIds}
                onChange={setAgentIds}
                loading={agentsLoading}
                error={agentsError}
                disabled={busy}
                onRetry={onRetryAgents}
              />
            </FieldSet>
          </FieldGroup>
          {saveError ? <Alert variant="destructive"><AlertDescription className="wrap-anywhere">{saveError}</AlertDescription></Alert> : null}
          <DialogFooter>
            {onDelete ? (
              <Button type="button" variant="ghost" className="sm:mr-auto" disabled={busy} onClick={onDelete}>
                Delete subnet
              </Button>
            ) : null}
            <Button type="button" variant="outline" onClick={() => changeOpen(false)} disabled={busy}>Cancel</Button>
            <Button type="submit" disabled={busy || membersBlocked}>
              {busy ? 'Saving…' : network ? 'Save changes' : 'Create subnet'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
