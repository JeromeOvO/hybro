'use client'

import { useRef, useLayoutEffect } from 'react'

interface ScrollRangeSpacerProps {
  scrollContainerRef: React.RefObject<HTMLDivElement | null>
}

export function ScrollRangeSpacer({ scrollContainerRef }: ScrollRangeSpacerProps) {
  const ref = useRef<HTMLDivElement>(null)

  useLayoutEffect(() => {
    const container = scrollContainerRef.current
    const spacer = ref.current
    if (!container || !spacer) return

    const recalc = () => {
      const containerH = container.clientHeight

      const contentEnd = container.querySelector('[data-content-end]') as HTMLElement | null

      const lastUserSticky = container.querySelectorAll<HTMLElement>('[data-message-id].sticky')
      const lastUser = lastUserSticky.length > 0
        ? lastUserSticky[lastUserSticky.length - 1]
        : null

      if (lastUser && contentEnd) {
        const userTop = lastUser.getBoundingClientRect().top
        const endTop = contentEnd.getBoundingClientRect().top
        const distUserToEnd = endTop - userTop
        spacer.style.height = `${Math.max(0, containerH - distUserToEnd)}px`
      } else {
        const group = spacer.parentElement
        if (!group) { spacer.style.height = '0px'; return }
        const contentH = group.scrollHeight - spacer.clientHeight
        spacer.style.height = `${Math.max(0, containerH - contentH)}px`
      }
    }

    recalc()

    const ro = new ResizeObserver(recalc)
    ro.observe(container)
    const group = spacer.parentElement
    if (group) ro.observe(group)

    return () => ro.disconnect()
  }, [scrollContainerRef])

  return <div ref={ref} data-scroll-spacer />
}
