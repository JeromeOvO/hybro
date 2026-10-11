import { LayoutDashboard, MessagesSquare, Plug, Waypoints } from 'lucide-react'
import type { NavAgentItem } from '@/lib/nav-items'
import { routes } from '@/lib/routes'

export const CONSUMER_NAV: NavAgentItem[] = [
  { name: 'Overview', url: routes.overview, icon: LayoutDashboard, colorClass: '', exact: true },
  { name: 'Network', url: routes.network, icon: Waypoints, colorClass: '' },
  { name: 'Room Chat', url: routes.chat, activePaths: ['/room'], icon: MessagesSquare, colorClass: '' },
  { name: 'Connect', url: routes.connectMcp, activePaths: ['/connect'], icon: Plug, colorClass: '' },
]
