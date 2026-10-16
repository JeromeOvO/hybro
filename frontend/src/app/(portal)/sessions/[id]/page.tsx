'use client'

import { useParams } from 'next/navigation'
import { RequireAuth } from '@/components/require-auth'
import { SessionRecordView } from '@/components/sessions/session-record-view'

export default function SessionRoute() {
  const params = useParams<{ id: string }>()
  return (
    <RequireAuth>
      <SessionRecordView key={params.id} sessionId={params.id} />
    </RequireAuth>
  )
}
