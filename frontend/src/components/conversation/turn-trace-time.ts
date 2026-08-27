import { useEffect, useMemo, useState } from 'react'

export function formatTurnDuration(durationMs: number | undefined): string {
  if (durationMs === undefined || !Number.isFinite(durationMs)) return '0.0s'
  const safeMs = Math.max(0, durationMs)
  if (safeMs < 60_000) return `${(safeMs / 1000).toFixed(1)}s`
  const totalSeconds = Math.floor(safeMs / 1000)
  const totalMinutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  if (totalMinutes < 60) return `${totalMinutes}m ${String(seconds).padStart(2, '0')}s`
  const hours = Math.floor(totalMinutes / 60)
  const minutes = totalMinutes % 60
  return `${hours}h ${String(minutes).padStart(2, '0')}m`
}

export function useTurnDurationLabel({
  startedAt,
  durationMs,
  live,
}: {
  startedAt?: string
  durationMs?: number
  live: boolean
}): string {
  const startedAtMs = useMemo(() => {
    if (!startedAt) return undefined
    const parsed = Date.parse(startedAt)
    return Number.isFinite(parsed) ? parsed : undefined
  }, [startedAt])
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (!live || startedAtMs === undefined || durationMs !== undefined) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [durationMs, live, startedAtMs])

  const elapsed = durationMs ?? (
    live && startedAtMs !== undefined ? Math.max(0, now - startedAtMs) : undefined
  )
  return formatTurnDuration(elapsed)
}
