import { Suspense } from 'react'
import type { Metadata } from 'next'
import { RequireAuth } from '@/components/require-auth'
import { SessionsTablePage } from '@/components/sessions/session-table'

export const metadata: Metadata = { title: 'Sessions – Hybro' }

export default function SessionsRoute() {
  return (
    <RequireAuth>
      <Suspense fallback={null}>
        <SessionsTablePage />
      </Suspense>
    </RequireAuth>
  )
}
