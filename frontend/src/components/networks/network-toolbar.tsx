'use client'

import Link from 'next/link'
import { ChevronDown, Link2, Loader2, Plus, Radar, Search } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { SegmentedControl } from '@/components/ui/segmented-control'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { routes } from '@/lib/routes'
import type { NetworkView } from '@/lib/network-url-state'

export function NetworkToolbar({ view, q, discovering, onViewChange, onSearch, onDiscover, onCreateSubnet }: {
  view: NetworkView
  q: string
  discovering: boolean
  onViewChange: (view: NetworkView) => void
  onSearch: (q: string) => void
  onDiscover: () => void
  onCreateSubnet: () => void
}) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <SegmentedControl<NetworkView>
        label="View"
        value={view}
        options={[{ value: 'topology', label: 'Topology' }, { value: 'structure', label: 'Structure' }]}
        onValueChange={onViewChange}
      />
      <div className="relative min-w-48 flex-1">
        <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
        <Input
          type="search"
          aria-label="Search the network"
          placeholder="Find an agent, skill, or subnet"
          value={q}
          onChange={event => onSearch(event.target.value)}
          className="h-9 rounded-lg pl-9"
        />
      </div>
      <div className="flex gap-2 max-sm:w-full max-sm:[&>*]:flex-1">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="secondary">
              {discovering ? <Loader2 className="animate-spin" data-icon="inline-start" aria-hidden="true" /> : <Plus data-icon="inline-start" aria-hidden="true" />}
              Add agent
              <ChevronDown aria-hidden="true" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-72">
            <DropdownMenuItem asChild>
              <Link href={routes.registerAgent}>
                <Link2 aria-hidden="true" />
                <span className="flex flex-col">
                  <span>Add Remote Agent</span>
                  <span className="text-xs text-muted-foreground">Add a remote A2A agent by its card URL.</span>
                </span>
              </Link>
            </DropdownMenuItem>
            <DropdownMenuItem disabled={discovering} onSelect={onDiscover}>
              <Radar aria-hidden="true" />
              <span className="flex flex-col">
                <span>{discovering ? 'Scanning Local Agent…' : 'Scan Local Agent'}</span>
                <span className="text-xs text-muted-foreground">Scan this machine for running agents.</span>
              </span>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        <Button onClick={onCreateSubnet}>
          <Plus data-icon="inline-start" aria-hidden="true" />
          Create subnet
        </Button>
      </div>
    </div>
  )
}
