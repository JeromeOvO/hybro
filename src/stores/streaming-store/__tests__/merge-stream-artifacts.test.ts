import { describe, expect, it } from 'vitest'
import { mergeStreamArtifacts } from '../merge-stream-artifacts'
import type { ArtifactData } from '@/stores/message-store/types'
import { extractStreamTextFromArtifacts } from '@/stores/message-store/upsert'

function textArtifact(
  artifactId: string,
  text: string,
  name = 'response',
): ArtifactData {
  return {
    artifactId,
    name,
    parts: [{ kind: 'text', text }],
  }
}

describe('mergeStreamArtifacts', () => {
  it('concatenates disjoint same-name segments (Hermes multi-paragraph)', () => {
    let list = mergeStreamArtifacts(undefined, textArtifact('seg-1', 'First paragraph. '), false)
    list = mergeStreamArtifacts(list, textArtifact('seg-2', 'Second paragraph. '), false)
    list = mergeStreamArtifacts(list, textArtifact('seg-3', 'Third paragraph.'), false)
    expect(extractStreamTextFromArtifacts(list)).toBe(
      'First paragraph. Second paragraph. Third paragraph.',
    )
  })

  it('appends token deltas on the same artifactId', () => {
    let list = mergeStreamArtifacts(undefined, textArtifact('stream-1', 'Hello'), false)
    list = mergeStreamArtifacts(list, textArtifact('stream-1', ' world'), true)
    expect(extractStreamTextFromArtifacts(list)).toBe('Hello world')
  })

  it('replaces prefix-related same-name segments with different artifactIds', () => {
    let list = mergeStreamArtifacts(undefined, textArtifact('tok-1', 'Hello'), false)
    list = mergeStreamArtifacts(list, textArtifact('tok-2', 'Hello world'), false)
    expect(list).toHaveLength(1)
    expect(extractStreamTextFromArtifacts(list)).toBe('Hello world')
  })

  it('ignores stale shorter same-name snapshots', () => {
    let list = mergeStreamArtifacts(undefined, textArtifact('tok-1', 'Hello world'), false)
    list = mergeStreamArtifacts(list, textArtifact('tok-2', 'Hello'), false)
    expect(list).toHaveLength(1)
    expect(extractStreamTextFromArtifacts(list)).toBe('Hello world')
  })

  it('ignores stale shorter same artifactId snapshots when append is false', () => {
    let list = mergeStreamArtifacts(undefined, textArtifact('art-1', 'Hello world'), false)
    list = mergeStreamArtifacts(list, textArtifact('art-1', 'Hello'), false)
    expect(extractStreamTextFromArtifacts(list)).toBe('Hello world')
  })

  it('pushes non-text artifacts alongside text segments', () => {
    let list = mergeStreamArtifacts(undefined, textArtifact('text-1', 'Report body'), false)
    list = mergeStreamArtifacts(list, {
      artifactId: 'file-1',
      name: 'output.csv',
      parts: [{ kind: 'file', file: { uri: 's3://bucket/file.csv' } }],
    }, false)
    expect(list).toHaveLength(2)
    expect(extractStreamTextFromArtifacts(list)).toBe('Report body')
  })

  it('replaces same artifactId when append is false', () => {
    let list = mergeStreamArtifacts(undefined, textArtifact('art-1', 'Draft'), false)
    list = mergeStreamArtifacts(list, textArtifact('art-1', 'Final'), false)
    expect(extractStreamTextFromArtifacts(list)).toBe('Final')
  })
})
