'use client'

import { useState } from 'react'
import { AlertCircle } from 'lucide-react'

import { getFileIcon } from '@/lib/file-icon-utils'
import { previewKind } from '@/lib/file-preview-policy'
import type { AttachmentData } from '@/lib/types/attachments'
import { useRoomFile } from '@/hooks/useRoomFile'
import { ImageLightbox } from '../image-lightbox'

function UnavailableBanner({ icon: Icon }: {
  icon: React.ComponentType<{ className?: string }>
}) {
  return (
    <div className="inline-flex items-center gap-2 rounded-md border border-dashed border-border bg-muted/50 px-2.5 py-1.5 text-xs text-muted-foreground">
      <Icon className="h-3.5 w-3.5 shrink-0" />
      <AlertCircle className="h-3 w-3 shrink-0" />
      <span>File unavailable</span>
    </div>
  )
}

export function UserAttachmentCard({ attachment }: { attachment: AttachmentData }) {
  const [downloadError, setDownloadError] = useState(false)
  const kind = previewKind(attachment.mimeType, attachment.sizeBytes)
  const { objectUrl, error, download } = useRoomFile(attachment.fileId, kind !== null)
  const sizeLabel = attachment.sizeBytes < 1024 * 1024
    ? `${(attachment.sizeBytes / 1024).toFixed(0)} KB`
    : `${(attachment.sizeBytes / (1024 * 1024)).toFixed(1)} MB`

  if (kind === 'image' && objectUrl) {
    return (
      <div className="max-w-[200px]">
        <ImageLightbox
          src={objectUrl}
          alt={attachment.fileName}
          className="max-w-[200px]"
        />
      </div>
    )
  }

  if (kind === 'audio' && objectUrl) {
    return (
      <div className="my-1">
        <audio controls preload="metadata" className="max-w-full">
          <source src={objectUrl} type={attachment.mimeType} />
        </audio>
        <span className="mt-1 block text-xs text-muted-foreground">
          {attachment.fileName} · {sizeLabel}
        </span>
      </div>
    )
  }

  if (kind === 'video' && objectUrl) {
    return (
      <div className="my-1">
        <video controls preload="metadata" className="max-w-full max-h-60 rounded-md border border-border">
          <source src={objectUrl} type={attachment.mimeType} />
        </video>
        <span className="mt-1 block text-xs text-muted-foreground">
          {attachment.fileName} · {sizeLabel}
        </span>
      </div>
    )
  }

  const { icon: Icon, color } = getFileIcon(
    attachment.mimeType,
    attachment.fileName,
  )
  if ((kind !== null && error) || downloadError) {
    return <UnavailableBanner icon={Icon} />
  }

  return (
    <button
      type="button"
      onClick={() => {
        setDownloadError(false)
        void download(attachment.fileName).catch(() => setDownloadError(true))
      }}
      className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs hover:bg-muted transition-colors"
    >
      <Icon className={`h-4 w-4 shrink-0 ${color}`} />
      <span className="truncate max-w-[120px]">{attachment.fileName}</span>
      <span className="text-muted-foreground">{sizeLabel}</span>
    </button>
  )
}
