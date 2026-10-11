'use client'

import { SlidersHorizontal } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import type { GraphDisplayPrefs } from '@/lib/graph-labels'

export function GraphDisplayMenu({ value, onChange }: { value: GraphDisplayPrefs; onChange: (next: GraphDisplayPrefs) => void }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label="Display options"><SlidersHorizontal /></Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" side="top">
        <DropdownMenuLabel>Labels</DropdownMenuLabel>
        <DropdownMenuCheckboxItem
          checked={value.alwaysSubnetLabels}
          onCheckedChange={checked => onChange({ ...value, alwaysSubnetLabels: checked === true })}
          onSelect={event => event.preventDefault()}
        >
          Always show subnet labels
        </DropdownMenuCheckboxItem>
        <DropdownMenuCheckboxItem
          checked={value.alwaysAgentLabels}
          onCheckedChange={checked => onChange({ ...value, alwaysAgentLabels: checked === true })}
          onSelect={event => event.preventDefault()}
        >
          Always show agent labels
        </DropdownMenuCheckboxItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
