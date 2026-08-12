import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '../../../utils/test-utils'
import { SynthesisContentBody } from '@/components/conversation/SynthesisContent'
import type { ArtifactData } from '@/stores/message-store/types'

vi.mock('@/components/markdown-content', () => ({
  MarkdownContent: ({ content }: { content: string }) => <div data-testid="markdown">{content}</div>,
}))

vi.mock('@/components/artifact-list', () => ({
  ArtifactList: ({ artifacts }: { artifacts: ArtifactData[] }) => (
    <div data-testid="artifact-list">
      {artifacts.map(a => (
        <div key={a.artifactId} data-testid={`artifact-${a.artifactId}`}>
          {a.parts.map((p, i) => (
            <span key={i} data-testid={`part-${p.kind}`}>{p.kind}</span>
          ))}
        </div>
      ))}
    </div>
  ),
}))

afterEach(() => {
  cleanup()
})

function textArtifact(id: string): ArtifactData {
  return {
    artifactId: id,
    parts: [{ kind: 'text', text: 'synthesis text' }],
  }
}

function fileArtifact(id: string): ArtifactData {
  return {
    artifactId: id,
    parts: [{ kind: 'file', file: { fileId: 'file-1', mime_type: 'image/png' } }],
  }
}

describe('SynthesisContentBody artifact fallback', () => {
  it('renders delegated image from turnArtifacts when own artifacts are text-only', () => {
    render(
      <SynthesisContentBody
        content="Here is the result."
        isStreaming={false}
        artifacts={[textArtifact('synth-text-1')]}
        turnArtifacts={[fileArtifact('delegated-image-1')]}
      />
    )

    const artifactList = screen.getByTestId('artifact-list')
    expect(artifactList).toBeTruthy()
    expect(screen.getByTestId('artifact-delegated-image-1')).toBeTruthy()
    expect(screen.queryByTestId('artifact-synth-text-1')).toBeNull()
  })

  it('merges non-text artifacts from both sources and deduplicates', () => {
    const sharedFile = fileArtifact('shared-file-1')

    render(
      <SynthesisContentBody
        content="Combined result."
        isStreaming={false}
        artifacts={[textArtifact('text-1'), sharedFile]}
        turnArtifacts={[sharedFile, fileArtifact('extra-image-1')]}
      />
    )

    const artifactList = screen.getByTestId('artifact-list')
    expect(artifactList).toBeTruthy()
    expect(screen.getByTestId('artifact-shared-file-1')).toBeTruthy()
    expect(screen.getByTestId('artifact-extra-image-1')).toBeTruthy()
    // shared-file-1 should appear once (deduplicated)
    expect(screen.getAllByTestId('artifact-shared-file-1')).toHaveLength(1)
  })

  it('shows only own non-text artifacts when turnArtifacts is undefined', () => {
    render(
      <SynthesisContentBody
        content="Just own artifacts."
        isStreaming={false}
        artifacts={[textArtifact('text-1'), fileArtifact('own-file-1')]}
      />
    )

    expect(screen.getByTestId('artifact-own-file-1')).toBeTruthy()
    expect(screen.queryByTestId('artifact-text-1')).toBeNull()
  })

  it('hides artifact list when no non-text artifacts exist anywhere', () => {
    render(
      <SynthesisContentBody
        content="Text only."
        isStreaming={false}
        artifacts={[textArtifact('text-1')]}
        turnArtifacts={[textArtifact('turn-text-1')]}
      />
    )

    expect(screen.queryByTestId('artifact-list')).toBeNull()
  })
})
