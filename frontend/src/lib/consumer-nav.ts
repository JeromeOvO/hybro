import { Bot, Code2, Globe, MessageCirclePlus, Network, Plug } from 'lucide-react'
import type { NavAgentItem } from '@/lib/nav-items'
import { routes } from '@/lib/routes'

export const CONSUMER_NAV: NavAgentItem[] = [
  {
    name: 'Chat',
    url: routes.chat,
    activePaths: ['/room'],
    icon: MessageCirclePlus,
    colorClass: 'text-icon-create',
  },
  {
    name: 'Network',
    icon: Globe,
    colorClass: 'text-icon-network',
    items: [
      { name: 'Agents', url: routes.agents, icon: Bot, colorClass: 'text-icon-network' },
      { name: 'Subnets', url: routes.networks, icon: Network, colorClass: 'text-icon-network' },
    ],
  },
  {
    name: 'Access',
    icon: Plug,
    colorClass: 'text-icon-workflow',
    items: [
      { name: 'MCP', url: routes.accessMcp, icon: Plug, colorClass: 'text-icon-workflow' },
      { name: 'API', url: routes.accessApi, icon: Code2, colorClass: 'text-icon-workflow' },
    ],
  },
]
