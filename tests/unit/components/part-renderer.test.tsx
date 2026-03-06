import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { PartRenderer } from '@/components/part-renderer'
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

  it('renders data part as collapsible JSON pre', () => {
    const data = { key: 'value', nested: { a: 1 } }
    const part: ArtifactPart = { kind: 'data', data }
    const { container } = render(<PartRenderer part={part} />)

    const details = container.querySelector('details')
    expect(details).toBeTruthy()
    expect(screen.getByText('Structured data')).toBeTruthy()

    const pre = container.querySelector('pre')
    expect(pre).toBeTruthy()
    expect(pre!.textContent).toBe(JSON.stringify(data, null, 2))
  })

  it('renders nothing for unknown kind', () => {
    const part = { kind: 'unknown' } as unknown as ArtifactPart
    const { container } = render(<PartRenderer part={part} />)

    expect(container.innerHTML).toBe('')
  })
})
