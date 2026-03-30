'use client'

import { House, Cloud } from 'lucide-react'
import { cn } from '@/lib/utils'
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from '@/components/ui/tooltip'

interface AgentSourceBadgeProps {
  source?: 'cloud' | 'hub'
  isHubOnline?: boolean
  className?: string
}

export function AgentSourceBadge({
  source,
  isHubOnline,
  className,
}: AgentSourceBadgeProps) {
  if (source === 'hub') {
    const online = isHubOnline ?? false
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <House
            className={cn(
              'shrink-0',
              online
                ? 'text-emerald-500'
                : 'text-muted-foreground/50',
              className
            )}
          />
        </TooltipTrigger>
        <TooltipContent side="top">
          {online
            ? 'Local agent'
            : 'Hub offline \u2014 start your hub to use this agent'}
        </TooltipContent>
      </Tooltip>
    )
  }

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Cloud
          className={cn('shrink-0 text-sky-500', className)}
        />
      </TooltipTrigger>
      <TooltipContent side="top">
        Cloud agent
      </TooltipContent>
    </Tooltip>
  )
}
