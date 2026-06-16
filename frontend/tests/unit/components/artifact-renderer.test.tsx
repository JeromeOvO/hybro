import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { ArtifactData } from '@/stores/message-store/types'

vi.mock('@/components/part-renderer', () => ({
  PartRenderer: ({ part }: any) => (
    <div data-testid="part-renderer" data-kind={part.kind} />
  ),
}))

import { ArtifactRenderer } from '@/components/artifact-renderer'

describe('ArtifactRenderer', () => {
  it('renders artifact name in header', () => {
    const artifact: ArtifactData = {
      artifactId: 'art-1',
      name: 'My Report',
      parts: [],
    }
    render(<ArtifactRenderer artifact={artifact} />)

    expect(screen.getByText('My Report')).toBeTruthy()
  })

  it('shows streaming indicator when streaming', () => {
    const artifact: ArtifactData = {
      artifactId: 'art-2',
      name: 'Streaming Artifact',
      parts: [],
      isStreaming: true,
    }
    const { container } = render(<ArtifactRenderer artifact={artifact} />)

    const spinner = container.querySelector('.animate-spin')
    expect(spinner).toBeTruthy()
  })

  it('renders one PartRenderer per part', () => {
    const artifact: ArtifactData = {
      artifactId: 'art-3',
      name: 'Multi-part',
      parts: [
        { kind: 'text', text: 'hello' },
        { kind: 'data', data: { x: 1 } },
        { kind: 'file', file: { uri: 'https://example.com/f.txt', mime_type: 'text/plain' } },
      ],
    }
    render(<ArtifactRenderer artifact={artifact} />)

    const renderers = screen.getAllByTestId('part-renderer')
    expect(renderers).toHaveLength(3)
    expect(renderers[0].getAttribute('data-kind')).toBe('text')
    expect(renderers[1].getAttribute('data-kind')).toBe('data')
    expect(renderers[2].getAttribute('data-kind')).toBe('file')
  })
})
