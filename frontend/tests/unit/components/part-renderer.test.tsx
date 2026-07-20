import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { CollapsibleJsonBlock, PartRenderer } from '@/components/part-renderer'
import type { ArtifactPart } from '@/stores/message-store/types'

describe('PartRenderer', () => {
  it('renders text part as paragraph', () => {
    const part: ArtifactPart = { kind: 'text', text: 'Hello world' }
    render(<PartRenderer part={part} />)

    const p = screen.getByText('Hello world')
    expect(p.tagName).toBe('P')
  })

  it('renders image file as img with alt', () => {
    const part: ArtifactPart = {
      kind: 'file',
      file: { uri: 'https://example.com/pic.png', mime_type: 'image/png', name: 'pic.png' },
    }
    const { container } = render(<PartRenderer part={part} />)
    const img = container.querySelector('img')

    expect(img).toBeTruthy()
    expect(img!.getAttribute('src')).toBe('https://example.com/pic.png')
    expect(img!.getAttribute('alt')).toBe('pic.png')
  })

  it('renders audio file as audio element', () => {
    const part: ArtifactPart = {
      kind: 'file',
      file: { uri: 'https://example.com/clip.mp3', mime_type: 'audio/mpeg', name: 'clip.mp3' },
    }
    const { container } = render(<PartRenderer part={part} />)
    const audio = container.querySelector('audio')

    expect(audio).toBeTruthy()
    const source = audio!.querySelector('source')
    expect(source!.getAttribute('src')).toBe('https://example.com/clip.mp3')
    expect(source!.getAttribute('type')).toBe('audio/mpeg')
  })

  it('renders video file as video element', () => {
    const part: ArtifactPart = {
      kind: 'file',
      file: { uri: 'https://example.com/video.mp4', mime_type: 'video/mp4', name: 'video.mp4' },
    }
    const { container } = render(<PartRenderer part={part} />)
    const video = container.querySelector('video')

    expect(video).toBeTruthy()
    const source = video!.querySelector('source')
    expect(source!.getAttribute('src')).toBe('https://example.com/video.mp4')
    expect(source!.getAttribute('type')).toBe('video/mp4')
  })

  it('renders generic file as download link', () => {
    const part: ArtifactPart = {
      kind: 'file',
      file: { uri: 'https://example.com/doc.pdf', mime_type: 'application/pdf', name: 'doc.pdf' },
    }
    const { container } = render(<PartRenderer part={part} />)
    const link = container.querySelector('a')

    expect(link).toBeTruthy()
    expect(link!.getAttribute('href')).toBe('https://example.com/doc.pdf')
    expect(link!.getAttribute('target')).toBe('_blank')
    expect(screen.getByText('doc.pdf')).toBeTruthy()
  })

  it('does not render inline file bytes without a URI', () => {
    const privateBytes = 'PRIVATE_SENTINEL_renderer_file_bytes'
    const part: ArtifactPart = {
      kind: 'file',
      file: { bytes: privateBytes, mime_type: 'image/png', name: 'private.png' },
    }
    const { container } = render(<PartRenderer part={part} />)

    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('audio')).toBeNull()
    expect(container.querySelector('video')).toBeNull()
    expect(container.querySelector('a')).toBeNull()
    expect(container.innerHTML).not.toContain(privateBytes)
    expect(container.innerHTML).not.toContain('data:image/png;base64')
  })

  it('renders data part as collapsible JSON block', () => {
    const data = { key: 'value', nested: { a: 1 } }
    const part: ArtifactPart = { kind: 'data', data }
    const { container } = render(<PartRenderer part={part} />)

    // Uses Radix Collapsible (button trigger), not a native <details> element
    const trigger = container.querySelector('[data-slot="collapsible-trigger"]')
    expect(trigger).toBeTruthy()

    // Trigger shows "JSON" label and a line count hint
    expect(trigger!.textContent).toContain('JSON')
    const lineCount = JSON.stringify(data, null, 2).split('\n').length
    expect(trigger!.textContent).toContain(`· ${lineCount} lines`)
  })

  it('renders text part containing raw JSON as collapsible JSON block', () => {
    const data = { foo: 'bar', count: 42 }
    const part: ArtifactPart = { kind: 'text', text: JSON.stringify(data) }
    const { container } = render(<PartRenderer part={part} />)

    const trigger = container.querySelector('[data-slot="collapsible-trigger"]')
    expect(trigger).toBeTruthy()
    expect(trigger!.textContent).toContain('JSON')
  })

  it('renders only one collapsible trigger when a JSON block is open', () => {
    const data = { foo: 'bar', count: 42 }
    const { container } = render(
      <CollapsibleJsonBlock data={data} open={true} onOpenChange={() => {}} />
    )

    expect(container.querySelectorAll('[data-slot="collapsible-trigger"]')).toHaveLength(1)
  })

  it('renders text part containing a JSON array as collapsible JSON block', () => {
    const data = [1, 2, { key: 'val' }]
    const part: ArtifactPart = { kind: 'text', text: JSON.stringify(data) }
    const { container } = render(<PartRenderer part={part} />)

    const trigger = container.querySelector('[data-slot="collapsible-trigger"]')
    expect(trigger).toBeTruthy()
    expect(trigger!.textContent).toContain('JSON')
  })

  it('does NOT render collapsible for text that looks like JSON but is invalid', () => {
    const part: ArtifactPart = { kind: 'text', text: '{broken json: true,}' }
    const { container } = render(<PartRenderer part={part} />)

    expect(container.querySelector('[data-slot="collapsible-trigger"]')).toBeNull()
  })

  it('does NOT render collapsible for plain non-JSON text', () => {
    const part: ArtifactPart = { kind: 'text', text: 'Just a normal message.' }
    const { container } = render(<PartRenderer part={part} />)

    expect(container.querySelector('[data-slot="collapsible-trigger"]')).toBeNull()
  })

  it('renders JSON text as markdown (not collapsible) while streaming', () => {
    const data = { status: 'streaming' }
    const part: ArtifactPart = { kind: 'text', text: JSON.stringify(data) }
    const { container } = render(<PartRenderer part={part} isStreaming={true} />)

    expect(container.querySelector('[data-slot="collapsible-trigger"]')).toBeNull()
  })

  it('renders nothing for unknown kind', () => {
    const part = { kind: 'unknown' } as unknown as ArtifactPart
    const { container } = render(<PartRenderer part={part} />)

    expect(container.innerHTML).toBe('')
  })
})
