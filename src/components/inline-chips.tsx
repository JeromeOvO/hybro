'use client'

interface InlineChipsProps {
  eventCount?: number
  durationMs?: number
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

export function InlineChips({ eventCount, durationMs }: InlineChipsProps) {
  const chips: { label: string; ariaText: string }[] = []

  if (eventCount != null) {
    const label = `${eventCount} step${eventCount !== 1 ? 's' : ''}`
    chips.push({ label, ariaText: label })
  }

  if (durationMs != null) {
    const label = formatDuration(durationMs)
    const seconds = (durationMs / 1000).toFixed(1)
    chips.push({ label, ariaText: `${seconds} seconds` })
  }

  if (chips.length === 0) return <span className="inline-flex items-center gap-1.5" />

  return (
    <span
      className="inline-flex items-center gap-1.5"
      aria-label={chips.map(c => c.ariaText).join(', ')}
    >
      {chips.map((chip) => (
        <span
          key={chip.label}
          className="inline-flex bg-secondary rounded px-1.5 py-px text-[10px] text-muted-foreground"
        >
          {chip.label}
        </span>
      ))}
    </span>
  )
}
