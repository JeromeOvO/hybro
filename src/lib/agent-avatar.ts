import { createAvatar } from '@dicebear/core'
import { bottts } from '@dicebear/collection'
import { isSystemAgent } from './system-agents'

const cache = new Map<string, string>()

export function getAgentAvatarUri(seed: string): string {
  if (isSystemAgent(seed)) {
    return '/favicon.svg'
  }

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

