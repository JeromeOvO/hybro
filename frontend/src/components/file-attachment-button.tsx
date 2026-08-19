'use client'

import { useRef } from 'react'
import { Paperclip } from 'lucide-react'
import { Button } from '@/components/ui/button'
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
const MAX_FILE_SIZE = 5 * 1024 * 1024
const MAX_ATTACHMENTS = 10

export { ACCEPTED_MIME_SET, MAX_FILE_SIZE, MAX_ATTACHMENTS }

interface FileAttachmentButtonProps {
  onFiles: (files: File[]) => void
  disabled?: boolean
  /** When true, the button stays visible but clicks are no-ops. */
  readOnly?: boolean
  className?: string
}

export function FileAttachmentButton({
  onFiles,
  disabled,
  readOnly = false,
  className,
}: FileAttachmentButtonProps) {
  const inputRef = useRef<HTMLInputElement>(null)

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (readOnly) return
    const files = Array.from(e.target.files || [])
    if (files.length > 0) {
      onFiles(files)
    }
    if (inputRef.current) inputRef.current.value = ''
  }

  return (
    <>
      <TooltipProvider delayDuration={200}>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              disabled={disabled}
              aria-label="Add photos and files"
              className={cn(
                'h-8 w-8 rounded-full text-muted-foreground hover:text-foreground transition-colors',
                className,
                readOnly && 'cursor-default hover:text-muted-foreground hover:bg-transparent',
              )}
              onClick={readOnly ? undefined : () => inputRef.current?.click()}
            >
              <Paperclip className="h-4 w-4" data-testid="attachment-upload-icon" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="top">
            Add photos and files
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
      {!readOnly && (
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={ACCEPTED_TYPES}
          onChange={handleChange}
          className="hidden"
        />
      )}
    </>
  )
}
