'use client'

import React from 'react'
import { Check, X, Loader2, Pause, Info } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { RailItemView, RailIcon } from '@/stores/turn-event-store/types'

function RailIconComponent({ icon, isActive }: { icon: RailIcon; isActive: boolean }) {
  const size = 'h-3 w-3'
  switch (icon) {
    case 'spinner':
      return <Loader2 className={cn(size, 'text-muted-foreground animate-spin')} data-testid="rail-spinner" />
    case 'check':
      return <Check className={cn(size, 'text-green-500')} />
    case 'x':
      return <X className={cn(size, 'text-destructive')} />
    case 'pause':
      return <Pause className={cn(size, 'text-amber-500')} />
    case 'info':
      return <Info className={cn(size, 'text-muted-foreground')} />
  }
}

interface OrchestrationRailProps {
  items: RailItemView[]
}

export const OrchestrationRail = React.memo(function OrchestrationRail({ items }: OrchestrationRailProps) {
  if (items.length === 0) return null

  return (
    <div className="mt-2 pl-10 pr-2" data-testid="orchestration-rail">
      <div className="border-l-2 border-muted pl-3 space-y-0.5">
        {items.map(item => (
          <div
            key={item.key}
            className={cn(
              'flex items-center gap-1.5 py-0.5',
              item.isActive ? 'text-foreground' : 'text-muted-foreground',
            )}
          >
            <RailIconComponent icon={item.icon} isActive={item.isActive} />
            <span className="text-xs">{item.label}</span>
          </div>
        ))}
      </div>
    </div>
  )
})
