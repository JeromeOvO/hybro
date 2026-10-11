import { redirect } from 'next/navigation'
import { routes } from '@/lib/routes'

export default function LegacyNetworksPage() {
  redirect(routes.network)
}
