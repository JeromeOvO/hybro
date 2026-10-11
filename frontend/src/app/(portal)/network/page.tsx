import { Suspense } from 'react'
import type { Metadata } from 'next'
import { NetworkWorkspace } from '@/components/networks/network-workspace'

export const metadata: Metadata = { title: 'Network – Hybro' }

export default function NetworkPage() {
  return (
    <Suspense fallback={null}>
      <NetworkWorkspace />
    </Suspense>
  )
}
