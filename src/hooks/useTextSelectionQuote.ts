'use client'

import { useEffect, useRef } from 'react'
import type { RefObject } from 'react'
import type { QuoteData, QuoteSourceKind } from '@/lib/types/quote'
import { getSelectionPlainText } from '@/lib/selection-plain-text'

/**
 * Manages a floating "Quote" button that appears when the user selects text
 * inside an agent message. Uses native DOM for performance (no React re-renders
 * on selection changes).
 *
 * Agent content elements must carry `data-quote-message-id` and
 * `data-quote-agent-name` attributes for the hook to identify quotable regions.
 */
export function useTextSelectionQuote(
  containerRef: RefObject<HTMLElement | null>,
  onQuote: ((data: QuoteData) => void) | undefined,
): void {
  const btnRef = useRef<HTMLButtonElement | null>(null)

  useEffect(() => {
    if (!onQuote) return
    const container = containerRef.current
    if (!container) return

    const emitQuote = onQuote

    function hideButton() {
      if (btnRef.current) {
        btnRef.current.remove()
        btnRef.current = null
      }
    }

    function showButton(rect: DOMRect, data: QuoteData) {
      hideButton()

      const btn = document.createElement('button')
      btn.textContent = '❝ Quote'
      Object.assign(btn.style, {
        position: 'fixed',
        zIndex: '9999',
        top: `${rect.top - 44}px`,
        left: `${rect.left + rect.width / 2}px`,
        transform: 'translateX(-50%)',
        padding: '8px 16px',
        borderRadius: '9px',
        fontSize: '13px',
        fontWeight: '700',
        lineHeight: '1',
        color: '#000',
        background: '#fff',
        border: 'none',
        boxShadow: '0 4px 20px rgba(0,0,0,.35), 0 0 0 1px rgba(0,0,0,.08)',
        cursor: 'pointer',
        whiteSpace: 'nowrap' as const,
        userSelect: 'none' as const,
      })

      btn.addEventListener('mousedown', (e) => {
        e.preventDefault()
        e.stopPropagation()
      })

      btn.addEventListener('click', () => {
        emitQuote(data)
        window.getSelection()?.removeAllRanges()
        hideButton()
      })

      document.body.appendChild(btn)
      btnRef.current = btn
    }

    function findQuoteAncestor(node: Node | null): HTMLElement | null {
      let el: HTMLElement | null = node instanceof HTMLElement ? node : node?.parentElement ?? null
      while (el && el !== container) {
        if (el.dataset.quoteMessageId) return el
        el = el.parentElement
      }
      return null
    }

    function handleMouseUp() {
      requestAnimationFrame(() => {
        const sel = window.getSelection()
        if (!sel || sel.isCollapsed || !sel.rangeCount) {
          hideButton()
          return
        }

        const range = sel.getRangeAt(0)
        const text = getSelectionPlainText(container)
        if (!text) {
          hideButton()
          return
        }

        const quotable = findQuoteAncestor(range.startContainer)
        if (!quotable) {
          hideButton()
          return
        }

        const messageId = quotable.dataset.quoteMessageId!
        const senderName = quotable.dataset.quoteAgentName ?? 'Agent'
        const rawKind = quotable.dataset.quoteSourceKind
        const sourceKind: QuoteSourceKind | undefined =
          rawKind === 'agent' || rawKind === 'synthesis' || rawKind === 'user_turn' || rawKind === 'unknown'
            ? rawKind
            : undefined

        const rect = range.getBoundingClientRect()
        showButton(rect, { messageId, content: text, senderName, sourceKind })
      })
    }

    function handleGlobalMouseDown(e: MouseEvent) {
      if (btnRef.current && !btnRef.current.contains(e.target as Node)) {
        hideButton()
      }
    }

    function handleScroll() {
      hideButton()
    }

    container.addEventListener('mouseup', handleMouseUp)
    container.addEventListener('scroll', handleScroll, true)
    document.addEventListener('mousedown', handleGlobalMouseDown)

    return () => {
      container.removeEventListener('mouseup', handleMouseUp)
      container.removeEventListener('scroll', handleScroll, true)
      document.removeEventListener('mousedown', handleGlobalMouseDown)
      hideButton()
    }
  }, [containerRef, onQuote])
}
