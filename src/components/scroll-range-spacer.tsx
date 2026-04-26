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

    const group = spacer.parentElement
    if (!group) return

    const recalc = () => {
      const containerH = container.clientHeight
      const contentH = group.scrollHeight - spacer.clientHeight
      spacer.style.height = `${Math.max(0, containerH - contentH)}px`
    }

    recalc()

    const ro = new ResizeObserver(recalc)
    ro.observe(container)
    ro.observe(group)

    return () => ro.disconnect()
  }, [scrollContainerRef])

  return <div ref={ref} data-scroll-spacer />
}
