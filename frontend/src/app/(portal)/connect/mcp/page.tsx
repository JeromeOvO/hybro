import { Suspense } from 'react'
import type { Metadata } from 'next'
import { ConnectPage } from '@/components/connect/connect-page'

export const metadata: Metadata = { title: 'Connect – Hybro' }

export default function ConnectMcpPage() {
  return <Suspense fallback={null}><ConnectPage key="mcp" kind="mcp" /></Suspense>
}
