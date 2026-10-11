import { cn } from '@/lib/utils'
import type { StatusTone, StatusView } from '@/lib/status'

const FILL: Record<StatusTone, string> = {
  online: 'bg-status-online',
  offline: 'bg-status-offline',
  unverified: 'bg-status-unverified',
  warning: 'bg-status-warning',
  error: 'bg-status-error',
}
const RING: Record<StatusTone, string> = {
  online: 'border-status-online',
  offline: 'border-status-offline',
  unverified: 'border-status-unverified',
  warning: 'border-status-warning',
  error: 'border-status-error',
}

export function StatusIndicator({ view, hideLabel = false, className }: {
  view: StatusView
  hideLabel?: boolean
  className?: string
}) {
  return (
    <span className={cn('inline-flex items-center gap-1.5 text-xs', className)}>
      <span
        aria-hidden="true"
        className={cn(
          'size-2 shrink-0 rounded-full',
          view.dot === 'filled' && FILL[view.tone],
          view.dot === 'hollow' && cn('border-[1.5px] border-dashed', RING[view.tone]),
          // Reduced motion shows a static filled dot instead of the spinning ring.
          view.dot === 'spinner' && cn(
            'border-[1.5px] border-t-transparent motion-safe:animate-spin motion-reduce:border-0 motion-reduce:bg-status-offline',
            RING[view.tone],
          ),
        )}
      />
      <span className={cn(hideLabel ? 'sr-only' : 'text-muted-foreground', !hideLabel && view.colorLabel && 'text-status-warning')}>
        {view.label}
      </span>
    </span>
  )
}
