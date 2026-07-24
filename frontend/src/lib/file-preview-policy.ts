export const MAX_INLINE_PREVIEW_BYTES = 10 * 1024 * 1024

const INLINE_IMAGE_TYPES = new Set([
  'image/png',
  'image/jpeg',
  'image/gif',
  'image/webp',
])
const INLINE_AUDIO_TYPES = new Set([
  'audio/wav',
  'audio/mpeg',
  'audio/mp4',
  'audio/webm',
])
const INLINE_VIDEO_TYPES = new Set([
  'video/mp4',
  'video/webm',
])

export function normalizeMimeType(mimeType?: string): string {
  return (mimeType || '').split(';', 1)[0].trim().toLowerCase()
}

export function previewKind(
  mimeType: string | undefined,
  sizeBytes: number | undefined,
): 'image' | 'audio' | 'video' | null {
  if ((sizeBytes ?? 0) > MAX_INLINE_PREVIEW_BYTES) return null
  const normalized = normalizeMimeType(mimeType)
  if (INLINE_IMAGE_TYPES.has(normalized)) return 'image'
  if (INLINE_AUDIO_TYPES.has(normalized)) return 'audio'
  if (INLINE_VIDEO_TYPES.has(normalized)) return 'video'
  return null
}
