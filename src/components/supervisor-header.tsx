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
    if (stepNumber != null && totalSteps != null) {
      parts.push(`Step ${stepNumber} of ${totalSteps}`)
    }
    // Never inline `details` (supervisor taskContent): it is internal orchestration text
    // and can be thousands of characters — it must not appear in the transcript.
    if (parts.length === 0) {
      parts.push('Working…')
    }
    statusText = parts.join(' · ')
  }

  const detailTitle =
    details && details.trim().length > 0
      ? details.length > 2000
        ? `${details.slice(0, 2000)}…`
        : details
      : undefined

  return (
    <div
      role="status"
      aria-live="polite"
      title={detailTitle}
      className="flex items-center gap-2 py-1.5 mb-1"
    >
      <img
        src="/favicon.svg"
        alt="HYBRO AI"
        className="w-4 h-4 shrink-0 opacity-90"
      />
      <span className="text-brand-gradient text-[11px] font-semibold tracking-wide">HYBRO AI</span>
      <span className="text-muted-foreground/40 text-[10px]">·</span>
      {isCompleted ? (
        <span className="text-[11px] text-muted-foreground">{statusText}</span>
      ) : (
        <span className="shimmer-text text-[11px] text-muted-foreground">{statusText}</span>
      )}
    </div>
  )
}
