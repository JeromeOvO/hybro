'use client'

import { Loader2, Package } from 'lucide-react'
import type { ArtifactData } from '@/stores/message-store/types'
import { PartRenderer } from './part-renderer'

interface ArtifactRendererProps {
  artifact: ArtifactData
}

const INTERNAL_ARTIFACT_NAMES = new Set(['streaming-multimodal', 'response', 'Response files'])
const HEX_ID_RE = /^[0-9a-f]{24,}$/i
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i

function isDisplayableArtifactName(name: string | undefined): name is string {
  if (!name) return false
  if (INTERNAL_ARTIFACT_NAMES.has(name)) return false
  if (HEX_ID_RE.test(name)) return false
  if (UUID_RE.test(name)) return false
  return true
}

export function ArtifactRenderer({ artifact }: ArtifactRendererProps) {
  const displayName = isDisplayableArtifactName(artifact.name) ? artifact.name : undefined
  const showHeader = displayName || artifact.isStreaming

  return (
    <div className="rounded-lg border border-border bg-card p-3">
      {showHeader && (
        <div className="mb-2 flex items-center gap-2 text-xs text-muted-foreground">
          <Package className="h-3.5 w-3.5" />
          {displayName && <span className="font-medium">{displayName}</span>}
          {artifact.isStreaming && (
            <Loader2 className="h-3 w-3 animate-spin" />
          )}
        </div>
      )}
      <div className="space-y-1">
        {artifact.parts.map((part, i) => (
          <PartRenderer key={`${artifact.artifactId}-part-${i}`} part={part} />
        ))}
      </div>
    </div>
  )
}
