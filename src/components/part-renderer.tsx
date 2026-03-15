'use client'

import { useState } from 'react'
import { FileIcon, Code2, ImageIcon, Volume2, Film, AlertCircle } from 'lucide-react'
import type { ArtifactPart } from '@/stores/message-store/types'
import { isPresignedUrlExpired } from '@/lib/presigned-url'
import { MarkdownContent } from './markdown-content'

const INTERNAL_NAME_RE = /^(inline|notify|ext)-\d+\.\w+$/
const UUID_DASHED_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i
const UUID_HEX_RE = /^[0-9a-f]{32}(\.\w+)?$/i

function isDisplayableName(name: string | undefined): name is string {
  if (!name) return false
  if (INTERNAL_NAME_RE.test(name)) return false
  if (UUID_DASHED_RE.test(name)) return false
  if (UUID_HEX_RE.test(name)) return false
  return true
}

function ResourceExpiredBanner({ icon: Icon }: { icon: React.ComponentType<{ className?: string }> }) {
  return (
    <div className="my-1 flex items-center gap-2 rounded-md border border-dashed border-border bg-muted/50 px-3 py-2 text-sm text-muted-foreground">
      <Icon className="h-4 w-4 shrink-0" />
      <AlertCircle className="h-3.5 w-3.5 shrink-0" />
      <span>This resource is no longer available</span>
    </div>
  )
}

function TextPartView({ text, isStreaming }: { text: string; isStreaming?: boolean }) {
  return <MarkdownContent content={text} isStreaming={isStreaming} />
}

function FilePartView({ file }: { file: NonNullable<ArtifactPart['file']> }) {
  const [loadError, setLoadError] = useState(false)
  const mime = file.mime_type || ''
  const isImage = mime.startsWith('image/')
  const isAudio = mime.startsWith('audio/')
  const isVideo = mime.startsWith('video/')
  const src = file.uri || (file.bytes ? `data:${mime || 'application/octet-stream'};base64,${file.bytes}` : null)
  const displayName = isDisplayableName(file.name) ? file.name : undefined

  if (isImage && src) {
    if (loadError) {
      return <ResourceExpiredBanner icon={ImageIcon} />
    }
    return (
      <div className="my-1">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={src}
          alt={displayName || 'image'}
          className="max-w-full max-h-80 rounded-md border border-border"
          loading="lazy"
          onError={() => setLoadError(true)}
        />
        {displayName && (
          <span className="mt-1 block text-xs text-muted-foreground">{displayName}</span>
        )}
      </div>
    )
  }

  if (isAudio && src) {
    if (loadError) {
      return <ResourceExpiredBanner icon={Volume2} />
    }
    return (
      <div className="my-1">
        <audio controls preload="metadata" className="max-w-full" onError={() => setLoadError(true)}>
          <source src={src} type={mime} />
        </audio>
        {displayName && (
          <span className="mt-1 block text-xs text-muted-foreground">{displayName}</span>
        )}
      </div>
    )
  }

  if (isVideo && src) {
    if (loadError) {
      return <ResourceExpiredBanner icon={Film} />
    }
    return (
      <div className="my-1">
        <video controls preload="metadata" className="max-w-full max-h-80 rounded-md border border-border" onError={() => setLoadError(true)}>
          <source src={src} type={mime} />
        </video>
        {displayName && (
          <span className="mt-1 block text-xs text-muted-foreground">{displayName}</span>
        )}
      </div>
    )
  }

  if (src) {
    return <GenericFileLink src={src} displayName={displayName} />
  }

  const FallbackIcon = isAudio ? Volume2 : isVideo ? Film : ImageIcon
  return (
    <div className="inline-flex items-center gap-1.5 text-sm text-muted-foreground">
      <FallbackIcon className="h-4 w-4" />
      <span>{displayName || 'File (no source)'}</span>
    </div>
  )
}

function GenericFileLink({ src, displayName }: { src: string; displayName: string | undefined }) {
  if (isPresignedUrlExpired(src)) {
    return <ResourceExpiredBanner icon={FileIcon} />
  }

  return (
    <a
      href={src}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-2 text-sm hover:bg-muted transition-colors"
    >
      <FileIcon className="h-4 w-4 text-muted-foreground" />
      <span>{displayName || 'Download file'}</span>
    </a>
  )
}

function DataPartView({ data }: { data: Record<string, unknown> }) {
  return (
    <details className="my-1">
      <summary className="inline-flex cursor-pointer items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
        <Code2 className="h-3.5 w-3.5" />
        Structured data
      </summary>
      <pre className="mt-1 overflow-x-auto rounded-md bg-muted p-2 text-xs">
        {JSON.stringify(data, null, 2)}
      </pre>
    </details>
  )
}

export function PartRenderer({ part, isStreaming }: { part: ArtifactPart; isStreaming?: boolean }) {
  switch (part.kind) {
    case 'text':
      return part.text ? <TextPartView text={part.text} isStreaming={isStreaming} /> : null
    case 'file':
      return part.file ? <FilePartView file={part.file} /> : null
    case 'data':
      return part.data ? <DataPartView data={part.data} /> : null
    default:
      return null
  }
}
