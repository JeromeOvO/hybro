import { redirect } from 'next/navigation'

import { routes } from '@/lib/routes'

export default async function LegacyManageAgentPage({
  params,
}: {
  params: Promise<{ id: string }>
}) {
  const { id } = await params
  redirect(routes.agent(id))
}
