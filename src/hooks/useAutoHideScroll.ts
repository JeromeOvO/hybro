import { useEffect, type RefObject } from 'react'

/**
 * Adds the `is-scrolling` CSS class to the referenced element while scrolling,
 * and removes it after a period of inactivity. This pairs with the auto-hiding
 * scrollbar styles in globals.css to show scrollbars only on hover or during
 * active scrolling.
 *
 * @param ref     - Ref to the scrollable container element.
 * @param timeout - Milliseconds of idle time before hiding (default: 1000).
 */
export function useAutoHideScroll(
  ref: RefObject<HTMLElement | null>,
  timeout = 1000,
) {
  useEffect(() => {
    const el = ref.current
    if (!el) return

    let timer: ReturnType<typeof setTimeout>

    const onScroll = () => {
      el.classList.add('is-scrolling')
      clearTimeout(timer)
      timer = setTimeout(() => el.classList.remove('is-scrolling'), timeout)
    }

    el.addEventListener('scroll', onScroll, { passive: true })
    return () => {
      el.removeEventListener('scroll', onScroll)
      clearTimeout(timer)
    }
  }, [ref, timeout])
}
