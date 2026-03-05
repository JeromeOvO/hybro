'use client'

import { FileIcon, Code2, ImageIcon, Volume2, Film } from 'lucide-react'
import type { ArtifactPart } from '@/stores/message-store/types'

function TextPartView({ text }: { text: string }) {
  return <p className="whitespace-pre-wrap text-sm">{text}</p>
}

function FilePartView({ file }: { file: NonNullable<ArtifactPart['file']> }) {
  const mime = file.mime_type || ''
  const isImage = mime.startsWith('image/')
  const isAudio = mime.startsWith('audio/')
  const isVideo = mime.startsWith('video/')
  const src = file.uri || (file.bytes ? `data:${mime || 'application/octet-stream'};base64,${file.bytes}` : null)

  if (isImage && src) {
    return (
      <div className="my-1">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={src}
          alt={file.name || 'image'}
          className="max-w-full max-h-80 rounded-md border border-border"
          loading="lazy"
        />
        {file.name && (
          <span className="mt-1 block text-xs text-muted-foreground">{file.name}</span>
        )}
      </div>
    )
  }

  if (isAudio && src) {
    return (
      <div className="my-1">
        <audio controls preload="metadata" className="max-w-full">
          <source src={src} type={mime} />
        </audio>
        {file.name && (
          <span className="mt-1 block text-xs text-muted-foreground">{file.name}</span>
        )}
      </div>
    )
  }

  if (isVideo && src) {
    return (
      <div className="my-1">
        <video controls preload="metadata" className="max-w-full max-h-80 rounded-md border border-border">
          <source src={src} type={mime} />
        </video>
        {file.name && (
          <span className="mt-1 block text-xs text-muted-foreground">{file.name}</span>
        )}
      </div>
    )
  }

  if (src) {
    return (
      <a
        href={src}
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-2 text-sm hover:bg-muted transition-colors"
      >
        <FileIcon className="h-4 w-4 text-muted-foreground" />
        <span>{file.name || 'Download file'}</span>
      </a>
    )
  }

  const FallbackIcon = isAudio ? Volume2 : isVideo ? Film : ImageIcon
  return (
    <div className="inline-flex items-center gap-1.5 text-sm text-muted-foreground">
      <FallbackIcon className="h-4 w-4" />
      <span>{file.name || 'File (no source)'}</span>
    </div>
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

export function PartRenderer({ part }: { part: ArtifactPart }) {
  switch (part.kind) {
    case 'text':
      return part.text ? <TextPartView text={part.text} /> : null
    case 'file':
      return part.file ? <FilePartView file={part.file} /> : null
    case 'data':
      return part.data ? <DataPartView data={part.data} /> : null
    default:
      return null
  }
}
