'use client'

import { X, Loader2, AlertCircle, Volume2, Film } from 'lucide-react'
import { cn } from '@/lib/utils'
import { getFileIcon } from '@/lib/file-icon-utils'
import type { PendingAttachment } from '@/lib/types/attachments'

interface AttachmentPreviewProps {
  attachments: PendingAttachment[]
  onRemove: (id: string) => void
}

function isImage(file: File): boolean {
  return file.type.startsWith('image/')
}

function AttachmentItem({ attachment, onRemove }: { attachment: PendingAttachment; onRemove: () => void }) {
  const isImg = isImage(attachment.file)
  const isAudio = attachment.file.type.startsWith('audio/')
  const isVideo = attachment.file.type.startsWith('video/')
  const mediaIcon = isAudio ? { icon: Volume2, color: 'text-muted-foreground' }
    : isVideo ? { icon: Film, color: 'text-muted-foreground' }
    : null
  const { icon: FallbackIcon, color: fallbackColor } = mediaIcon ?? getFileIcon(attachment.file.type, attachment.file.name)

  return (
    <div
      className={cn(
        'relative group flex items-center gap-2 rounded-lg border border-border bg-card px-2.5 py-2',
        attachment.status === 'error' && 'border-destructive/50 bg-destructive/5',
      )}
    >
      {isImg && attachment.previewUrl ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={attachment.previewUrl}
          alt={attachment.file.name}
          className="h-8 w-8 rounded object-cover"
        />
      ) : (
        <FallbackIcon className={`h-5 w-5 shrink-0 ${fallbackColor}`} />
      )}

      <div className="min-w-0 flex-1">
        <p className="truncate text-xs font-medium">{attachment.file.name}</p>
        <p className="text-[10px] text-muted-foreground">
          {(attachment.file.size / 1024).toFixed(0)} KB
        </p>
      </div>

      {attachment.status === 'uploading' && (
        <Loader2 className="h-4 w-4 animate-spin text-primary shrink-0" />
      )}
      {attachment.status === 'error' && (
        <AlertCircle className="h-4 w-4 text-destructive shrink-0" />
      )}

      <button
        type="button"
        onClick={onRemove}
        className="absolute -right-1.5 -top-1.5 hidden group-hover:flex h-4 w-4 items-center justify-center rounded-full bg-muted text-muted-foreground hover:bg-destructive hover:text-destructive-foreground transition-colors"
      >
        <X className="h-2.5 w-2.5" />
      </button>
    </div>
  )
}

export function AttachmentPreview({ attachments, onRemove }: AttachmentPreviewProps) {
  if (attachments.length === 0) return null

  return (
    <div data-testid="attachment-preview" className="flex flex-wrap gap-2 px-5 pt-3">
      {attachments.map(att => (
        <AttachmentItem
          key={att.id}
          attachment={att}
          onRemove={() => onRemove(att.id)}
        />
      ))}
    </div>
  )
}
