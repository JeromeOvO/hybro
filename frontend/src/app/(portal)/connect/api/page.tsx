import { Suspense } from 'react'
import type { Metadata } from 'next'
import { ConnectPage } from '@/components/connect/connect-page'

export const metadata: Metadata = { title: 'Connect – Hybro' }

export default function ConnectApiPage() {
  return <Suspense fallback={null}><ConnectPage key="api" kind="api" /></Suspense>
}
