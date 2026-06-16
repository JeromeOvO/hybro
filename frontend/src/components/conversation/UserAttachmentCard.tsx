'use client'

import { useState } from 'react'
import { ImageIcon, Volume2, Film, AlertCircle } from 'lucide-react'
import { isPresignedUrlExpired } from '@/lib/presigned-url'
import type { AttachmentData } from '@/lib/types/attachments'
import { ImageLightbox } from '../image-lightbox'
import { getFileIcon } from '@/lib/file-icon-utils'

function AttachmentExpiredBanner({ icon: Icon }: { icon: React.ComponentType<{ className?: string }> }) {
  return (
    <div className="inline-flex items-center gap-2 rounded-md border border-dashed border-border bg-muted/50 px-2.5 py-1.5 text-xs text-muted-foreground">
      <Icon className="h-3.5 w-3.5 shrink-0" />
      <AlertCircle className="h-3 w-3 shrink-0" />
      <span>Resource expired</span>
    </div>
  )
}

function GenericAttachmentLink({ url, fileName, sizeLabel, mimeType }: { url: string; fileName: string; sizeLabel: string; mimeType?: string }) {
  if (isPresignedUrlExpired(url)) {
    return <AttachmentExpiredBanner icon={AlertCircle} />
  }

  const { icon: Icon, color } = getFileIcon(mimeType, fileName)

  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs hover:bg-muted transition-colors"
    >
      <Icon className={`h-4 w-4 shrink-0 ${color}`} />
      <span className="truncate max-w-[120px]">{fileName}</span>
      <span className="text-muted-foreground">{sizeLabel}</span>
    </a>
  )
}

export function UserAttachmentCard({ attachment }: { attachment: AttachmentData }) {
  const [loadError, setLoadError] = useState(false)
  const isImg = attachment.mimeType.startsWith('image/')
  const isAudio = attachment.mimeType.startsWith('audio/')
  const isVideo = attachment.mimeType.startsWith('video/')
  const sizeLabel = attachment.sizeBytes < 1024 * 1024
    ? `${(attachment.sizeBytes / 1024).toFixed(0)} KB`
    : `${(attachment.sizeBytes / (1024 * 1024)).toFixed(1)} MB`

  if (isImg && attachment.fileUrl) {
    if (loadError) return <AttachmentExpiredBanner icon={ImageIcon} />
    return (
      <div className="max-w-[200px]">
        <ImageLightbox
          src={attachment.fileUrl}
          alt={attachment.fileName}
          className="max-w-[200px]"
          onError={() => setLoadError(true)}
        />
      </div>
    )
  }

  if (isAudio && attachment.fileUrl) {
    if (loadError) return <AttachmentExpiredBanner icon={Volume2} />
    return (
      <div className="my-1">
        <audio controls preload="metadata" className="max-w-full" onError={() => setLoadError(true)}>
          <source src={attachment.fileUrl} type={attachment.mimeType} />
        </audio>
        <span className="mt-1 block text-xs text-muted-foreground">
          {attachment.fileName} · {sizeLabel}
        </span>
      </div>
    )
  }

  if (isVideo && attachment.fileUrl) {
    if (loadError) return <AttachmentExpiredBanner icon={Film} />
    return (
      <div className="my-1">
        <video controls preload="metadata" className="max-w-full max-h-60 rounded-md border border-border" onError={() => setLoadError(true)}>
          <source src={attachment.fileUrl} type={attachment.mimeType} />
        </video>
        <span className="mt-1 block text-xs text-muted-foreground">
          {attachment.fileName} · {sizeLabel}
        </span>
      </div>
    )
  }

  if (attachment.fileUrl) {
    return (
      <GenericAttachmentLink
        url={attachment.fileUrl}
        fileName={attachment.fileName}
        sizeLabel={sizeLabel}
        mimeType={attachment.mimeType}
      />
    )
  }

  const { icon: FallbackIcon, color: fallbackColor } = getFileIcon(attachment.mimeType, attachment.fileName)
  return (
    <span className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs text-muted-foreground">
      <FallbackIcon className={`h-4 w-4 shrink-0 ${fallbackColor}`} />
      <span className="truncate max-w-[120px]">{attachment.fileName}</span>
      <span>{sizeLabel}</span>
    </span>
  )
}
