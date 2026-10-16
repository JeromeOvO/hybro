import type { Metadata } from 'next'
import type { ReactNode } from 'react'

export const metadata: Metadata = { title: 'Session – Hybro' }

export default function SessionLayout({ children }: { children: ReactNode }) {
  return children
}
