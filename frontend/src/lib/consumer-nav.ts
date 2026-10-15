import { Inbox, LayoutDashboard, MessagesSquare, Plug, Waypoints } from 'lucide-react'
import type { NavAgentItem } from '@/lib/nav-items'
import { routes } from '@/lib/routes'

export const CONSUMER_NAV: NavAgentItem[] = [
  { name: 'Overview', url: routes.overview, icon: LayoutDashboard, colorClass: 'text-icon-architecture', exact: true },
  { name: 'Network', url: routes.network, icon: Waypoints, colorClass: 'text-icon-network' },
  { name: 'Room Chat', url: routes.chat, activePaths: ['/room'], icon: MessagesSquare, colorClass: 'text-icon-create' },
  { name: 'Sessions', url: routes.sessions, activePaths: ['/sessions'], icon: Inbox, colorClass: 'text-icon-network' },
  { name: 'Connect', url: routes.connectMcp, activePaths: ['/connect'], icon: Plug, colorClass: 'text-icon-workflow' },
]
