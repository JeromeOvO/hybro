'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { Code2, ImageIcon, Volume2, Film, AlertCircle, ChevronRight } from 'lucide-react'
import { getFileIcon } from '@/lib/file-icon-utils'
import type { ArtifactPart } from '@/stores/message-store/types'
import { isPresignedUrlExpired } from '@/lib/presigned-url'
import { tryParseJson } from '@/lib/utils'
import { MarkdownContent } from './markdown-content'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from './ui/collapsible'
import { ImageLightbox } from './image-lightbox'

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
  const wasStreaming = useRef(false)
  const [jsonOpen, setJsonOpen] = useState(false)

  useEffect(() => {
    if (isStreaming) wasStreaming.current = true
  }, [isStreaming])

  // Open the collapsible when streaming finishes and the final content is JSON
  useEffect(() => {
    if (!isStreaming && wasStreaming.current && tryParseJson(text) !== null) {
      setJsonOpen(true)
    }
  }, [isStreaming, text])

  if (!isStreaming) {
    const parsed = tryParseJson(text)
    if (parsed !== null) {
      return <CollapsibleJsonBlock data={parsed} open={jsonOpen} onOpenChange={setJsonOpen} />
    }
  }
  return (
    <div className="conversation-content-body">
      <MarkdownContent
        className="conversation-markdown-body"
        content={text}
        isStreaming={isStreaming}
      />
    </div>
  )
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
        <ImageLightbox
          src={src}
          alt={displayName || 'image'}
          caption={displayName}
          onError={() => setLoadError(true)}
        />
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
    return <GenericFileLink src={src} displayName={displayName} mimeType={mime} />
  }

  const FallbackIcon = isAudio ? Volume2 : isVideo ? Film : ImageIcon
  return (
    <div className="inline-flex items-center gap-1.5 text-sm text-muted-foreground">
      <FallbackIcon className="h-4 w-4" />
      <span>{displayName || 'File (no source)'}</span>
    </div>
  )
}

function GenericFileLink({ src, displayName, mimeType }: { src: string; displayName: string | undefined; mimeType?: string }) {
  const { icon: Icon, color } = getFileIcon(mimeType, displayName)

  if (isPresignedUrlExpired(src)) {
    return <ResourceExpiredBanner icon={Icon} />
  }

  return (
    <a
      href={src}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-2 text-sm hover:bg-muted transition-colors"
    >
      <Icon className={`h-4 w-4 shrink-0 ${color}`} />
      <span>{displayName || 'Download file'}</span>
    </a>
  )
}

export function CollapsibleJsonBlock({ data, open, onOpenChange }: {
  data: unknown
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const jsonString = useMemo(() => JSON.stringify(data, null, 2), [data])
  const lineCount = useMemo(() => jsonString.split('\n').length, [jsonString])
  const fenced = useMemo(() => '```json\n' + jsonString + '\n```', [jsonString])

  return (
    <Collapsible open={open} onOpenChange={onOpenChange} className="my-1">
      <CollapsibleTrigger className="inline-flex cursor-pointer items-center gap-1 py-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors">
        <ChevronRight
          className="h-3.5 w-3.5 transition-transform duration-150 ease-in-out"
          style={{ transform: open ? 'rotate(90deg)' : 'rotate(0deg)' }}
        />
        <Code2 className="h-3.5 w-3.5" />
        <span>JSON</span>
        <span className="text-muted-foreground/60">· {lineCount} {lineCount === 1 ? 'line' : 'lines'}</span>
      </CollapsibleTrigger>
      <CollapsibleContent className="data-[state=open]:animate-collapsible-down overflow-hidden">
        <div className="mt-1">
          <MarkdownContent content={fenced} autoFormatJson={false} />
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}

function DataPartView({ data }: { data: Record<string, unknown> }) {
  const [open, setOpen] = useState(false)
  return <CollapsibleJsonBlock data={data} open={open} onOpenChange={setOpen} />
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
