import { describe, expect, it } from 'vitest'

import {
  MAX_INLINE_PREVIEW_BYTES,
  normalizeMimeType,
  previewKind,
} from '@/lib/file-preview-policy'

describe('room file preview policy', () => {
  it('normalizes MIME parameters and casing', () => {
    expect(normalizeMimeType(' Image/PNG; charset=binary ')).toBe('image/png')
    expect(previewKind(' Image/PNG; charset=binary ', 12)).toBe('image')
  })

  it('previews only the explicit safe media allowlist', () => {
    expect(previewKind('image/svg+xml', 12)).toBeNull()
    expect(previewKind('text/html', 12)).toBeNull()
    expect(previewKind('application/pdf', 12)).toBeNull()
    expect(previewKind('video/mp4', 12)).toBe('video')
    expect(previewKind('audio/mpeg', 12)).toBe('audio')
  })

  it('downloads instead of previewing above ten MiB', () => {
    expect(previewKind('image/png', MAX_INLINE_PREVIEW_BYTES)).toBe('image')
    expect(previewKind('image/png', MAX_INLINE_PREVIEW_BYTES + 1)).toBeNull()
  })
})
