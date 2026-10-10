'use client'

import { useId, useMemo, useState } from 'react'
import { RotateCw } from 'lucide-react'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Checkbox } from '@/components/ui/checkbox'
import { Empty, EmptyDescription } from '@/components/ui/empty'
import { Field, FieldContent, FieldDescription, FieldGroup, FieldLabel, FieldLegend, FieldSet } from '@/components/ui/field'
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
    <FieldSet className="min-w-0 gap-3" disabled={disabled}>
      <FieldLegend className="sr-only">Select agents</FieldLegend>
      <Field data-disabled={disabled || loading}>
        <div className="flex items-center justify-between gap-3">
          <FieldLabel htmlFor={searchId}>Search agents</FieldLabel>
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
      </Field>
      {loading ? (
        <div className="flex flex-col gap-2" role="status" aria-label="Loading agents">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-full" />
          <span className="sr-only">Loading agents…</span>
        </div>
      ) : null}
      {error ? (
        <Alert variant="destructive">
          <AlertDescription className="gap-2 wrap-anywhere">
            <p>{error}</p>
            <Button type="button" variant="outline" size="sm" onClick={onRetry} disabled={disabled || loading}>
              <RotateCw data-icon="inline-start" aria-hidden="true" />
              {loading ? 'Retrying…' : 'Reload agents'}
            </Button>
          </AlertDescription>
        </Alert>
      ) : null}
      <FieldGroup data-slot="checkbox-group" className="max-h-[32dvh] overflow-y-auto p-1" aria-busy={loading}>
        {visible.map((agent, index) => {
          const added = existing.has(agent.id)
          const unavailable = agent.status !== 'active' && agent.status !== 'inactive'
          const checkboxId = `${searchId}-agent-${index}`
          const choiceDisabled = disabled || loading || Boolean(error) || added || (unavailable && !selected.has(agent.id))
          return (
            <Field key={agent.id} orientation="horizontal" data-disabled={choiceDisabled}>
              <Checkbox
                id={checkboxId}
                checked={added || selected.has(agent.id)}
                disabled={choiceDisabled}
                onCheckedChange={checked => toggle(agent, checked === true)}
                aria-label={`${added ? 'Already added:' : 'Select'} ${agent.name}`}
                aria-describedby={agent.description || unavailable ? `${checkboxId}-description` : undefined}
              />
              <FieldContent className="min-w-0">
                <FieldLabel htmlFor={checkboxId} className="wrap-anywhere">{agent.name}</FieldLabel>
                {agent.description || unavailable ? (
                  <FieldDescription id={`${checkboxId}-description`} className="wrap-anywhere">
                    {agent.description}
                    {agent.description && unavailable ? ' ' : null}
                    {unavailable ? `${agent.status === 'unknown' ? 'This agent’s details could not be verified.' : 'This agent is currently unavailable.'} Its membership is preserved.` : null}
                  </FieldDescription>
                ) : null}
                <div className="flex flex-wrap gap-1">
                  {added ? <Badge variant="secondary">Added</Badge> : null}
                  <Badge variant={agent.status === 'active' ? 'outline' : 'inactive'}>
                    {statusLabels[agent.status]}
                  </Badge>
                </div>
              </FieldContent>
            </Field>
          )
        })}
        {!loading && !error && visible.length === 0 ? (
          <Empty className="py-4 md:p-4">
            <EmptyDescription>{normalizedQuery ? 'No matching agents.' : 'No agents available to add.'}</EmptyDescription>
          </Empty>
        ) : null}
      </FieldGroup>
      {!loading && !error && !normalizedQuery && choices.length > 0 && !hasNewChoices ? (
        <FieldDescription>No more agents available to add.</FieldDescription>
      ) : null}
    </FieldSet>
  )
}
