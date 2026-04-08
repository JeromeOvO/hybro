'use client'

import { useRef } from 'react'
import { Plus, Paperclip } from 'lucide-react'
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
}

export function FileAttachmentButton({
  onFiles,
  disabled,
  className,
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
      <TooltipProvider delayDuration={200}>
        <DropdownMenu>
          <Tooltip>
            <TooltipTrigger asChild>
              <DropdownMenuTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  disabled={disabled}
                  className={cn('h-8 w-8 rounded-full text-muted-foreground hover:text-primary transition-colors', className)}
                >
                  <Plus className="h-4 w-4" />
                </Button>
              </DropdownMenuTrigger>
            </TooltipTrigger>
                    <TooltipContent side="top">
                      More options
                    </TooltipContent>
          </Tooltip>
          <DropdownMenuContent align="start" side="top" sideOffset={8} className="min-w-[220px]">
          <DropdownMenuItem
            onSelect={() => inputRef.current?.click()}
            className="gap-2 cursor-pointer"
          >
            <Paperclip className="h-4 w-4" />
            <span>Add photos and files</span>
          </DropdownMenuItem>
        </DropdownMenuContent>
        </DropdownMenu>
      </TooltipProvider>
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
