'use client'

import { useRef } from 'react'
import { Plus, Paperclip, ShipWheel, Swords } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
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

const ACCEPTED_MIME_SET = new Set([
  'image/png', 'image/jpeg', 'image/gif', 'image/webp',
  'audio/wav', 'audio/mpeg', 'audio/mp4', 'audio/webm',
  'video/mp4', 'video/webm',
  'text/plain', 'text/markdown', 'text/html', 'text/csv',
  'application/json', 'application/xml', 'application/pdf', 'application/zip',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
])
const ACCEPTED_TYPES = [...ACCEPTED_MIME_SET].join(',')
const MAX_FILE_SIZE = 50 * 1024 * 1024
const MAX_ATTACHMENTS = 10

export { ACCEPTED_MIME_SET, MAX_FILE_SIZE, MAX_ATTACHMENTS }

interface FileAttachmentButtonProps {
  onFiles: (files: File[]) => void
  disabled?: boolean
  className?: string
  supervisorMode?: boolean
  onSupervisorChange?: (enabled: boolean) => void
  debateMode?: boolean
  onDebateModeChange?: (enabled: boolean) => void
}

export function FileAttachmentButton({
  onFiles,
  disabled,
  className,
  supervisorMode,
  onSupervisorChange,
  debateMode,
  onDebateModeChange,
}: FileAttachmentButtonProps) {
  const inputRef = useRef<HTMLInputElement>(null)

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || [])
    if (files.length > 0) {
      onFiles(files)
    }
    if (inputRef.current) inputRef.current.value = ''
  }

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            disabled={disabled}
            className={cn('h-8 w-8 rounded-full text-muted-foreground hover:text-foreground', className)}
            title="Add attachments"
          >
            <Plus className="h-4 w-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" side="top" sideOffset={8} className="min-w-[220px]">
          <DropdownMenuItem
            onSelect={() => inputRef.current?.click()}
            className="gap-2 cursor-pointer"
          >
            <Paperclip className="h-4 w-4" />
            <span>Add photos and files</span>
          </DropdownMenuItem>

          {onSupervisorChange !== undefined && (
            <TooltipProvider delayDuration={300}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <DropdownMenuItem
                    onSelect={(e) => e.preventDefault()}
                    className="gap-2 cursor-pointer"
                  >
                    <ShipWheel className="h-4 w-4" />
                    <span className="flex-1">Supervisor Mode</span>
                    <Switch
                      checked={supervisorMode ?? false}
                      onCheckedChange={onSupervisorChange}
                      className="ml-2"
                    />
                  </DropdownMenuItem>
                </TooltipTrigger>
                <TooltipContent side="right" className="text-xs">
                  Supervisor understands your request better
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          )}

          {onDebateModeChange !== undefined && (
            <TooltipProvider delayDuration={300}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <DropdownMenuItem
                    onSelect={(e) => e.preventDefault()}
                    className="gap-2 cursor-pointer"
                  >
                    <Swords className="h-4 w-4" />
                    <span className="flex-1">Debate Mode</span>
                    <Switch
                      checked={debateMode ?? false}
                      onCheckedChange={onDebateModeChange}
                      className="ml-2"
                    />
                  </DropdownMenuItem>
                </TooltipTrigger>
                <TooltipContent side="right" className="text-xs">
                  Agents will debate with each other
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={ACCEPTED_TYPES}
        onChange={handleChange}
        className="hidden"
      />
    </>
  )
}
