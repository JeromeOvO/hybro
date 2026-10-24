import { describe, expect, it } from 'vitest'
import type { ArtifactData } from '@/stores/message-store/types'
import { withDistinctFileNames } from './distinct-file-names'

function fileArtifact(id: string, name: string): ArtifactData {
  return { artifactId: id, name, parts: [{ kind: 'file', file: { fileId: id, name } }] }
}

describe('withDistinctFileNames', () => {
  it('numbers repeated names and keeps file ids', () => {
    const result = withDistinctFileNames([
      fileArtifact('f1', 'document.docx'),
      fileArtifact('f2', 'document.docx'),
      fileArtifact('f3', 'README'),
      fileArtifact('f4', 'README'),
    ])

    expect(result.map((artifact) => artifact.name)).toEqual([
      'document.docx', 'document (2).docx', 'README', 'README (2)',
    ])
    expect(result[1].parts[0].file).toEqual({ fileId: 'f2', name: 'document (2).docx' })
  })

  it('returns unique lists unchanged', () => {
    const input = [fileArtifact('f1', 'a.docx'), fileArtifact('f2', 'b.docx')]
    const result = withDistinctFileNames(input)
    expect(result[0]).toBe(input[0])
    expect(result[1]).toBe(input[1])
  })
})
