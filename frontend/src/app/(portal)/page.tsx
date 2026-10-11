import type { Metadata } from 'next'
import { OverviewPage } from '@/components/overview/overview-page'

export const metadata: Metadata = { title: 'Overview – Hybro' }

export default function HomePage() {
  return <OverviewPage />
}
