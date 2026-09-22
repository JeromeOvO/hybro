'use client'

import { useId, useMemo, useState } from 'react'
import { RotateCw } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import type { AgentPickerProps, NetworkAgent } from './types'

const statusLabels: Record<NetworkAgent['status'], string> = {
  active: 'Active',
  inactive: 'Inactive',
  deleted: 'Deleted',
  unavailable: 'Unavailable',
  unknown: 'Unverified',
}

export function AgentPicker({
  agents,
  value,
  onChange,
  existingIds,
  loading,
  error,
  disabled = false,
  onRetry,
}: AgentPickerProps) {
  const searchId = useId()
  const [query, setQuery] = useState('')
  const selected = useMemo(() => new Set(value), [value])
  const existing = useMemo(() => new Set(existingIds), [existingIds])
  const choices = useMemo(() => {
    const byId = new Map(agents.map(agent => [agent.id, agent]))
    for (const id of [...existing, ...selected]) {
      if (!byId.has(id)) {
        byId.set(id, { id, name: id, description: '', skills: [], status: 'unknown' })
      }
    }
    return Array.from(byId.values()).filter(agent =>
      agent.status === 'active' || agent.status === 'inactive' || existing.has(agent.id) || selected.has(agent.id)
    )
  }, [agents, existing, selected])
  const normalizedQuery = query.trim().toLocaleLowerCase()
  const visible = choices.filter(agent =>
    `${agent.name} ${agent.id} ${agent.description} ${agent.skills.join(' ')}`.toLocaleLowerCase().includes(normalizedQuery)
  )
  const chosenCount = value.filter(id => !existing.has(id)).length
  const hasNewChoices = choices.some(agent => !existing.has(agent.id) && (agent.status === 'active' || agent.status === 'inactive'))

  function toggle(agent: NetworkAgent, checked: boolean) {
    if (disabled || loading || error || existing.has(agent.id)) return
    if (checked && agent.status !== 'active' && agent.status !== 'inactive') return
    const draft = value.filter(id => !existing.has(id))
    onChange(checked ? [...draft, agent.id] : draft.filter(id => id !== agent.id))
  }

  return (
    <fieldset className="flex min-w-0 flex-col gap-3" disabled={disabled}>
      <legend className="sr-only">Select agents</legend>
      <div className="flex items-center justify-between gap-3">
        <Label htmlFor={searchId}>Search agents</Label>
        <Badge variant="secondary" aria-live="polite">{chosenCount} selected</Badge>
      </div>
      <Input
        id={searchId}
        type="search"
        placeholder="Search agent names or skills"
        value={query}
        onChange={event => setQuery(event.target.value)}
        disabled={disabled || loading}
      />
      {loading ? (
        <div className="flex flex-col gap-2" role="status" aria-label="Loading agents">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-full" />
          <span className="sr-only">Loading agents…</span>
        </div>
      ) : null}
      {error ? (
        <div className="flex flex-col items-start gap-2">
          <p role="alert" className="text-sm text-destructive wrap-anywhere">{error}</p>
          <Button type="button" variant="outline" size="sm" onClick={onRetry} disabled={disabled || loading}>
            <RotateCw data-icon="inline-start" aria-hidden="true" />
            {loading ? 'Retrying…' : 'Reload agents'}
          </Button>
        </div>
      ) : null}
      <div className="flex max-h-[32dvh] flex-col gap-2 overflow-y-auto" aria-busy={loading}>
        {visible.map(agent => {
          const added = existing.has(agent.id)
          const unavailable = agent.status !== 'active' && agent.status !== 'inactive'
          return (
            <label key={agent.id} className="flex items-start gap-3 rounded-md border p-3">
              <input
                type="checkbox"
                className="mt-0.5 size-4 shrink-0 accent-primary"
                checked={added || selected.has(agent.id)}
                disabled={disabled || loading || Boolean(error) || added || (unavailable && !selected.has(agent.id))}
                onChange={event => toggle(agent, event.target.checked)}
                aria-label={`${added ? 'Already added:' : 'Select'} ${agent.name}`}
              />
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-medium wrap-anywhere">{agent.name}</span>
                {agent.description ? (
                  <span className="mt-1 block text-xs text-muted-foreground wrap-anywhere">{agent.description}</span>
                ) : null}
                {unavailable ? (
                  <span className="mt-1 block text-xs text-muted-foreground">
                    {agent.status === 'unknown' ? 'This agent’s details could not be verified.' : 'This agent is currently unavailable.'} Its membership is preserved.
                  </span>
                ) : null}
                <span className="mt-2 flex flex-wrap gap-1">
                  {added ? <Badge variant="secondary">Added</Badge> : null}
                  <Badge variant={agent.status === 'active' ? 'outline' : 'inactive'}>
                    {statusLabels[agent.status]}
                  </Badge>
                </span>
              </span>
            </label>
          )
        })}
        {!loading && !error && visible.length === 0 ? (
          <p className="py-4 text-sm text-muted-foreground">
            {normalizedQuery ? 'No matching agents.' : 'No agents available to add.'}
          </p>
        ) : null}
      </div>
      {!loading && !error && !normalizedQuery && choices.length > 0 && !hasNewChoices ? (
        <p className="text-xs text-muted-foreground">No more agents available to add.</p>
      ) : null}
    </fieldset>
  )
}
