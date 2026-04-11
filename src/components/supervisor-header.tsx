'use client'

interface SupervisorHeaderProps {
  isCompleted: boolean
  stepNumber?: number
  totalSteps?: number
  details?: string
  agentCount?: number
  totalDurationMs?: number
}

function formatDuration(ms: number): string {
  return `${(ms / 1000).toFixed(1)}s`
}

export function SupervisorHeader({
  isCompleted,
  stepNumber,
  totalSteps,
  details,
  agentCount,
  totalDurationMs,
}: SupervisorHeaderProps) {
  let statusText: string
  if (isCompleted) {
    const parts: string[] = []
    if (agentCount != null) parts.push(`${agentCount} agent${agentCount !== 1 ? 's' : ''}`)
    if (totalDurationMs != null) parts.push(formatDuration(totalDurationMs))
    statusText = parts.join(' · ') || 'Completed'
  } else {
    const parts: string[] = []
    if (stepNumber != null && totalSteps != null) parts.push(`Step ${stepNumber} of ${totalSteps}`)
    if (details) parts.push(details)
    statusText = parts.join(' · ') || 'Processing...'
  }

  return (
    <div
      role="status"
      aria-live="polite"
      className="flex items-center gap-2.5 pb-3 mb-3 border-b border-border"
    >
      <img
        src="/favicon.svg"
        alt="HYBRO AI"
        className="w-[18px] h-[18px] shrink-0"
      />
      <span className="text-brand-gradient text-xs font-semibold">HYBRO AI</span>
      <span className="text-muted-foreground/50 text-[11px]">·</span>
      {isCompleted ? (
        <span className="text-xs text-muted-foreground">{statusText}</span>
      ) : (
        <span className="shimmer-text text-xs text-muted-foreground">{statusText}</span>
      )}
    </div>
  )
}
