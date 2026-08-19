import type { Metadata } from 'next'
import { CorePageContent } from '@/components/portal/core-page-content'

export const metadata: Metadata = {
  title: 'Hybro Core – The Interoperability Engine for AI Agents',
  description:
    'Complete data privacy, zero configuration, and native A2A protocol support. All running on your machine. Apache 2.0 licensed.',
  openGraph: {
    title: 'Hybro Core – The Interoperability Engine for AI Agents',
    description:
      'Local-first multi-agent orchestration with A2A protocol support. Run AI agents privately with zero config.',
    url: 'https://hybro.ai/core',
  },
}

export default function CorePage() {
  return <CorePageContent />
}
