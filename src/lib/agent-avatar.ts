import { createAvatar } from '@dicebear/core'
import { bottts } from '@dicebear/collection'

const cache = new Map<string, string>()

export function getAgentAvatarUri(seed: string): string {
  const cached = cache.get(seed)
  if (cached) return cached

  const uri = createAvatar(bottts, {
    seed,
    size: 128,
    radius: 10,
    randomizeIds: true,
  }).toDataUri()

  cache.set(seed, uri)
  return uri
}
