'use client'

import { Loader2, Package } from 'lucide-react'
import type { ArtifactData } from '@/stores/message-store/types'
import { PartRenderer } from './part-renderer'

interface ArtifactRendererProps {
  artifact: ArtifactData
}

export function ArtifactRenderer({ artifact }: ArtifactRendererProps) {
  return (
    <div className="rounded-lg border border-border bg-card p-3">
      <div className="mb-2 flex items-center gap-2 text-xs text-muted-foreground">
        <Package className="h-3.5 w-3.5" />
        <span className="font-medium">{artifact.name || 'Artifact'}</span>
        {artifact.isStreaming && (
          <Loader2 className="h-3 w-3 animate-spin" />
        )}
      </div>
      <div className="space-y-1">
        {artifact.parts.map((part, i) => (
          <PartRenderer key={`${artifact.artifactId}-part-${i}`} part={part} />
        ))}
      </div>
    </div>
  )
}
