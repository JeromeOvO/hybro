'use client'

import { useState, useRef } from 'react'
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

const SELECTABLE_MODE_CONFIG = {
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
} as const

type SelectableChatMode = keyof typeof SELECTABLE_MODE_CONFIG

function toSelectableMode(mode: ChatMode): SelectableChatMode {
  return mode
}

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
  const currentMode = toSelectableMode(mode)
  const current = SELECTABLE_MODE_CONFIG[currentMode]
  const CurrentIcon = current.icon

  const [tooltipOpen, setTooltipOpen] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const ignoreTooltipRef = useRef(false)

  const handleTooltipOpenChange = (isOpen: boolean) => {
    if (menuOpen) {
      setTooltipOpen(false)
      return
    }
    if (isOpen && ignoreTooltipRef.current) {
      setTooltipOpen(false)
      return
    }
    setTooltipOpen(isOpen)
  }

  const handleModeSelect = (modeKey: ChatMode) => {
    ignoreTooltipRef.current = true
    setTooltipOpen(false)
    setMenuOpen(false)
    onModeChange(modeKey)
    setTimeout(() => {
      ignoreTooltipRef.current = false
    }, 500)
  }

  return (
    <div className={cn('flex items-center', className)}>
      <TooltipProvider delayDuration={100}>
        <DropdownMenu open={menuOpen} onOpenChange={(open) => {
          setMenuOpen(open)
          if (open) {
            setTooltipOpen(false)
            ignoreTooltipRef.current = true
          } else {
            setTimeout(() => {
              ignoreTooltipRef.current = false
            }, 300)
          }
        }}>
          <Tooltip open={menuOpen ? false : tooltipOpen} onOpenChange={handleTooltipOpenChange}>
            <TooltipTrigger asChild>
              <DropdownMenuTrigger asChild disabled={disabled}>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-8 min-h-8 min-w-0 max-w-full px-3 gap-1.5 font-normal hover:bg-muted/50 flex items-center border-none shadow-none focus-visible:ring-0 focus-visible:border-transparent"
                  onMouseLeave={() => {
                    ignoreTooltipRef.current = false
                  }}
                >
                  <CurrentIcon className={cn('h-3.5 w-3.5 shrink-0', current.iconColor)} />
                  <span className="min-w-0 truncate font-medium">{current.label}</span>
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
            className="min-w-[210px] border border-border/50 shadow-lg z-50 bg-background/95 backdrop-blur-md"
          >
            {Object.entries(SELECTABLE_MODE_CONFIG).map(([key, config]) => {
              const modeKey = key as SelectableChatMode
              const Icon = config.icon
              return (
                <Tooltip key={modeKey} delayDuration={150}>
                  <TooltipTrigger asChild>
                    <DropdownMenuItem
                      onClick={() => handleModeSelect(modeKey)}
                      className={cn(
                        'flex items-center gap-2.5 py-2',
                        currentMode === modeKey && 'bg-accent',
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
            <DropdownMenuItem disabled className="flex items-center gap-2.5 py-2">
              <Swords className="h-4 w-4 text-muted-foreground" />
              <span className="font-medium">Debate (Coming Soon)</span>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </TooltipProvider>
    </div>
  )
}

