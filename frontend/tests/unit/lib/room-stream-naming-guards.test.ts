import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync } from 'fs'
import type { Dirent } from 'fs'
import { join } from 'path'

// Naming guards for the Room Stream Snapshot plan (§10): no versioned
// branding in owned surfaces. Version-neutral names only.
const VERSIONED_BRANDING = [
  'sse_v2',
  'stream_v2',
  'protocol_v2',
  'sse-v2',
  'stream-v2',
  'protocol-v2',
  'SSE V2',
]

const OWNED_DIRS = [
  'src/hooks/room',
  'src/lib/room-sync',
  'src/lib/api',
  'src/lib/types',
  'src/stores/trace-store',
  'src/stores/message-store',
  'src/stores/streaming-store',
]

function ownedFiles(): string[] {
  const files: string[] = []
  for (const dir of OWNED_DIRS) {
    const absolute = join(process.cwd(), dir)
    const walk = (current: string): void => {
      for (const entry of readdirSync(current, { withFileTypes: true }) as Dirent[]) {
        const full = join(current, entry.name)
        if (entry.isDirectory()) {
          walk(full)
        } else if (/\.(ts|tsx|css)$/.test(entry.name)) {
          files.push(full)
        }
      }
    }
    walk(absolute)
  }
  return files
}

describe('room stream naming guards', () => {
  it('finds no versioned branding in owned surfaces', () => {
    const offenders: string[] = []
    for (const file of ownedFiles()) {
      const text = readFileSync(file, 'utf8')
      for (const branding of VERSIONED_BRANDING) {
        if (text.includes(branding)) offenders.push(`${file}: ${branding}`)
      }
    }
    expect(offenders).toEqual([])
  })

  it('keeps the four-key SSE envelope pinned', () => {
    // The envelope contract lives in src/lib/types/sse.ts (hasSSEFrameEnvelope):
    // {type, timestamp, room_id, data} — exactly four keys.
    const source = readFileSync(join(process.cwd(), 'src/lib/types/sse.ts'), 'utf8')
    expect(source).toContain("keys[0] === 'data'")
    expect(source).toContain("keys[1] === 'room_id'")
    expect(source).toContain("keys[2] === 'timestamp'")
    expect(source).toContain("keys[3] === 'type'")
    expect(source).toContain('keys.length === 4')
  })
})
