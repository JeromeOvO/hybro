import { describe, expect, it } from 'vitest'
import {
  acquireHitlSubmissionFence,
  hitlSubmissionFenceKey,
} from '@/hooks/room/hitl-submission-fence'

describe('HITL submission fence', () => {
  it('uses collision-safe room and interaction tuple identity', () => {
    expect(hitlSubmissionFenceKey('a:b', 'c')).not.toBe(
      hitlSubmissionFenceKey('a', 'b:c'),
    )
  })

  it('blocks only the exact in-flight tuple and always permits release', () => {
    const release = acquireHitlSubmissionFence('room-1', 'interaction-1')
    expect(() => acquireHitlSubmissionFence('room-1', 'interaction-1')).toThrow(
      'already being submitted',
    )
    const releaseOther = acquireHitlSubmissionFence('room-1', 'interaction-2')
    releaseOther()
    release()
    release() // idempotent cleanup is safe from every finally/error path
    const replayRelease = acquireHitlSubmissionFence('room-1', 'interaction-1')
    replayRelease()
  })
})
