'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useEffect, useSyncExternalStore } from 'react'
import { AlertTriangle, X } from 'lucide-react'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { useSetupStatus } from '@/hooks/useSetupStatus'
import { settingsHref } from '@/lib/settings-url'
import { isDismissed, readDismissed, setupNotice, subscribeDismissed, writeDismissed, type SetupNotice } from '@/lib/setup-notice'

function NoticeAlert({ notice, onDismiss }: { notice: SetupNotice; onDismiss?: () => void }) {
  const pathname = usePathname()
  return (
    <Alert data-testid="setup-banner" className="border-status-warning/40">
      <AlertTriangle className="text-status-warning" aria-hidden="true" />
      <AlertDescription className="flex flex-wrap items-center gap-x-3 gap-y-2 text-foreground">
        <span className="min-w-0 flex-1">{notice.message}</span>
        <span className="flex items-center gap-1">
          <Button variant="secondary" size="sm" asChild>
            <Link href={settingsHref(pathname, 'model-provider')}>Open model provider settings</Link>
          </Button>
          {onDismiss ? (
            <Button variant="ghost" size="icon" className="size-8" aria-label="Dismiss" onClick={onDismiss}>
              <X aria-hidden="true" />
            </Button>
          ) : null}
        </span>
      </AlertDescription>
    </Alert>
  )
}

/** Overview banner: dismissible per browser until the status reports a new error code. */
export function OverviewSetupBanner() {
  const status = useSetupStatus()
  const notice = setupNotice(status.data)
  const dismissed = useSyncExternalStore(subscribeDismissed, readDismissed, () => null)
  const healthy = status.isSuccess && notice === null

  useEffect(() => {
    // A healthy status ends the dismissal, so the next error shows again.
    if (healthy && readDismissed() !== null) writeDismissed(null)
  }, [healthy])

  if (!notice || isDismissed(notice, dismissed)) return null
  return <NoticeAlert notice={notice} onDismiss={() => writeDismissed(notice.code)} />
}

/** Room Chat banner: always shown while there is a notice. */
export function SetupBanner({ notice }: { notice: SetupNotice | null }) {
  return notice ? <NoticeAlert notice={notice} /> : null
}
