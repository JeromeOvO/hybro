import { redirect } from 'next/navigation'
import { routes } from '@/lib/routes'

export default function LegacyMcpAccessPage() {
  redirect(routes.connectMcp)
}
