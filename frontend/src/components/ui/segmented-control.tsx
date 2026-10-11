'use client'

import { useRef, type KeyboardEvent } from 'react'
import { cn } from '@/lib/utils'

export function SegmentedControl<T extends string>({ label, value, options, onValueChange, className }: {
  label: string
  value: T
  options: { value: T; label: string }[]
  onValueChange: (value: T) => void
  className?: string
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([])

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const step = event.key === 'ArrowRight' || event.key === 'ArrowDown' ? 1
      : event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? -1 : 0
    if (!step) return
    event.preventDefault()
    const next = (index + step + options.length) % options.length
    onValueChange(options[next].value)
    refs.current[next]?.focus()
  }

  return (
    <div role="radiogroup" aria-label={label} className={cn('inline-flex rounded-lg border border-input p-0.5', className)}>
      {options.map((option, index) => {
        const checked = option.value === value
        return (
          <button
            key={option.value}
            ref={element => { refs.current[index] = element }}
            type="button"
            role="radio"
            aria-checked={checked}
            tabIndex={checked ? 0 : -1}
            onClick={() => onValueChange(option.value)}
            onKeyDown={event => onKeyDown(event, index)}
            className={cn(
              'h-8 rounded-[6px] px-3 text-sm outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50',
              checked ? 'bg-surface-raised font-medium text-foreground shadow-xs' : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {option.label}
          </button>
        )
      })}
    </div>
  )
}
