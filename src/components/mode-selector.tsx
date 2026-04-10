'use client'

import { ChevronDown, Sparkles, Zap, Swords } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'
import type { ChatMode } from '@/lib/types/chat-mode'
import { CHAT_MODE } from '@/lib/types/chat-mode'

const MODE_CONFIG = {
  [CHAT_MODE.ULTIMATE]: {
    label: 'Ultimate',
    icon: Sparkles,
    iconColor: 'text-primary',
    description: 'For big tasks that need planning',
  },
  [CHAT_MODE.FAST]: {
    label: 'Fast',
    icon: Zap,
    iconColor: 'text-yellow-500',
    description: 'For quick and simple questions',
  },
  [CHAT_MODE.ULTIMATE_DEBATE]: {
    label: 'Ultimate - Debate',
    icon: Swords,
    iconColor: 'text-primary',
    description: 'For big tasks where different ideas should be compared',
  },
  [CHAT_MODE.FAST_DEBATE]: {
    label: 'Fast - Debate',
    icon: Swords,
    iconColor: 'text-yellow-500',
    description: 'For quick answers with extra checking',
  },
} as const

interface ModeSelectorProps {
  mode: ChatMode
  onModeChange: (mode: ChatMode) => void
  disabled?: boolean
  className?: string
}

export function ModeSelector({
  mode,
  onModeChange,
  disabled = false,
  className,
}: ModeSelectorProps) {
  const current = MODE_CONFIG[mode]
  const CurrentIcon = current.icon

  return (
    <div className={cn('flex items-center', className)}>
      <TooltipProvider delayDuration={100}>
        <DropdownMenu>
          <Tooltip>
            <TooltipTrigger asChild>
              <DropdownMenuTrigger asChild disabled={disabled}>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-8 min-h-8 px-3 gap-1.5 font-normal hover:bg-muted/50 flex items-center border-none shadow-none focus-visible:ring-0 focus-visible:border-transparent"
                >
                  <CurrentIcon className={cn('h-3.5 w-3.5', current.iconColor)} />
                  <span className="font-medium">{current.label}</span>
                  <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
                </Button>
              </DropdownMenuTrigger>
            </TooltipTrigger>
            <TooltipContent side="top">
              {current.description}
            </TooltipContent>
          </Tooltip>
          <DropdownMenuContent
            side="top"
            align="start"
            className="min-w-[140px] border border-border/50 shadow-lg z-50 bg-background/95 backdrop-blur-md"
          >
            {Object.entries(MODE_CONFIG).map(([key, config]) => {
              const modeKey = key as ChatMode
              const Icon = config.icon
              return (
                <Tooltip key={modeKey} delayDuration={150}>
                  <TooltipTrigger asChild>
                    <DropdownMenuItem
                      onClick={() => onModeChange(modeKey)}
                      className={cn(
                        'flex items-center gap-2.5 py-2',
                        mode === modeKey && 'bg-accent',
                      )}
                    >
                      <Icon className={cn('h-4 w-4', config.iconColor)} />
                      <span className="font-medium">{config.label}</span>
                    </DropdownMenuItem>
                  </TooltipTrigger>
                  <TooltipContent
                    side="right"
                    sideOffset={4}
                    className="max-w-xs text-xs"
                  >
                    {config.description}
                  </TooltipContent>
                </Tooltip>
              )
            })}
          </DropdownMenuContent>
        </DropdownMenu>
      </TooltipProvider>
    </div>
  )
}
