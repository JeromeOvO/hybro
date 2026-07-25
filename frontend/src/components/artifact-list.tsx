'use client'

import { useState } from 'react'
import { ChevronDown, ChevronUp } from 'lucide-react'
import type { ArtifactData } from '@/stores/message-store/types'
import { ArtifactRenderer } from './artifact-renderer'

interface ArtifactListProps {
  artifacts: ArtifactData[]
}

function hasContent(artifact: ArtifactData): boolean {
  if (artifact.isStreaming) return true
  return artifact.parts.some(p =>
    (p.kind === 'text' && !!p.text?.trim()) ||
    (p.kind === 'file' && !!p.file?.fileId) ||
    (p.kind === 'data' && p.data != null && Object.keys(p.data).length > 0)
  )
}

export function ArtifactList({ artifacts }: ArtifactListProps) {
  const [expanded, setExpanded] = useState(true)
  const nonEmpty = artifacts.filter(hasContent)

  if (nonEmpty.length === 0) return null

  if (nonEmpty.length === 1) {
    return (
      <div className="mt-2">
        <ArtifactRenderer artifact={nonEmpty[0]} />
      </div>
    )
  }

  return (
    <div className="mt-2 space-y-1">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
      >
        {expanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
        {nonEmpty.length} artifacts
      </button>
      {expanded && (
        <div className="space-y-2">
          {nonEmpty.map(artifact => (
            <ArtifactRenderer key={artifact.artifactId} artifact={artifact} />
          ))}
        </div>
      )}
    </div>
  )
}
