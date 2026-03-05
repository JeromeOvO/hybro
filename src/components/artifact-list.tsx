'use client'

import { useState } from 'react'
import { ChevronDown, ChevronUp } from 'lucide-react'
import type { ArtifactData } from '@/stores/message-store/types'
import { ArtifactRenderer } from './artifact-renderer'

interface ArtifactListProps {
  artifacts: ArtifactData[]
}

export function ArtifactList({ artifacts }: ArtifactListProps) {
  const [expanded, setExpanded] = useState(true)

  if (artifacts.length === 0) return null

  if (artifacts.length === 1) {
    return (
      <div className="mt-2">
        <ArtifactRenderer artifact={artifacts[0]} />
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
        {artifacts.length} artifacts
      </button>
      {expanded && (
        <div className="space-y-2">
          {artifacts.map(artifact => (
            <ArtifactRenderer key={artifact.artifactId} artifact={artifact} />
          ))}
        </div>
      )}
    </div>
  )
}
