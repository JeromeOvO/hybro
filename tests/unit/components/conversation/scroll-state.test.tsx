import { describe, expect, it } from 'vitest'
import { resolveScrollStateAfterEvent } from '@/components/conversation/scroll-state'

describe('resolveScrollStateAfterEvent', () => {
  it('treats interrupted programmatic upward scroll as a user pause', () => {
    const next = resolveScrollStateAfterEvent({
      atBottom: false,
      programmatic: true,
      previousScrollTop: 500,
      currentScrollTop: 460,
      wasPaused: false,
    })

    expect(next.programmatic).toBe(false)
    expect(next.paused).toBe(true)
    expect(next.clearNewContent).toBe(false)
  })

  it('keeps downward programmatic scroll active until it reaches bottom', () => {
    const next = resolveScrollStateAfterEvent({
      atBottom: false,
      programmatic: true,
      previousScrollTop: 460,
      currentScrollTop: 500,
      wasPaused: false,
    })

    expect(next.programmatic).toBe(true)
    expect(next.paused).toBe(false)
    expect(next.clearNewContent).toBe(false)
  })
})
