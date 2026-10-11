import { redirect } from 'next/navigation'
import { routes } from '@/lib/routes'

export default function LegacyAgentsPage() {
  redirect(routes.network)
}
