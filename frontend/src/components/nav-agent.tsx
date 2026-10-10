'use client'

import { useState } from 'react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { ChevronRight } from 'lucide-react'
import {
  SidebarGroup,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubItem,
  useSidebar,
} from '@/components/ui/sidebar'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import type { NavAgentGroup, NavAgentItem, NavAgentLink } from '@/lib/nav-items'
import { SIDEBAR_ICON_CENTER, SIDEBAR_ICON_HIDDEN } from '@/lib/sidebar-styles'

function isCurrentPath(pathname: string, url: string) {
  return pathname === url || pathname.startsWith(`${url}/`)
}

function MenuLink({ item, pathname }: { item: NavAgentLink; pathname: string }) {
  const { setOpenMobile } = useSidebar()
  const isActive = isCurrentPath(pathname, item.url)
    || Boolean(item.activePaths?.some(path => isCurrentPath(pathname, path)))

  return (
    <SidebarMenuButton asChild isActive={isActive} tooltip={item.name}>
      <Link href={item.url} prefetch={false} scroll={false} aria-current={isActive ? 'page' : undefined} onClick={() => setOpenMobile(false)}>
        <item.icon className={`transition-colors ${item.colorClass} ${SIDEBAR_ICON_CENTER}`} />
        <span className={`leading-7 ${SIDEBAR_ICON_HIDDEN}`}>{item.name}</span>
      </Link>
    </SidebarMenuButton>
  )
}

function MenuGroup({ item, pathname }: { item: NavAgentGroup; pathname: string }) {
  const { state, isMobile, setOpen } = useSidebar()
  const isActive = item.items.some(child => isCurrentPath(pathname, child.url))
  const [expanded, setExpanded] = useState(isActive)

  return (
    <Collapsible asChild open={expanded} onOpenChange={next => {
      if (!isMobile && state === 'collapsed') {
        setOpen(true)
        setExpanded(true)
      } else {
        setExpanded(next)
      }
    }} className="group/menu-group">
      <SidebarMenuItem>
        <CollapsibleTrigger asChild>
          <SidebarMenuButton isActive={isActive} tooltip={item.name} aria-label={item.name}>
            <item.icon className={`transition-colors ${item.colorClass} ${SIDEBAR_ICON_CENTER}`} />
            <span className={`flex-1 leading-7 ${SIDEBAR_ICON_HIDDEN}`}>{item.name}</span>
            <ChevronRight className={`ml-auto transition-transform group-data-[state=open]/menu-group:rotate-90 ${SIDEBAR_ICON_HIDDEN}`} />
          </SidebarMenuButton>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <SidebarMenuSub>
            {item.items.map(child => (
              <SidebarMenuSubItem key={child.url}>
                <MenuLink item={child} pathname={pathname} />
              </SidebarMenuSubItem>
            ))}
          </SidebarMenuSub>
        </CollapsibleContent>
      </SidebarMenuItem>
    </Collapsible>
  )
}

export function NavAgent({ navAgents }: { navAgents: NavAgentItem[] }) {
  const pathname = usePathname()

  return (
    <SidebarGroup className="max-h-[45dvh] shrink-0 overflow-y-auto">
      <nav aria-label="Modules">
        <SidebarMenu className="gap-1.5">
          {navAgents.map(item => 'items' in item ? (
            <MenuGroup key={item.name} item={item} pathname={pathname} />
          ) : (
            <SidebarMenuItem key={item.name}>
              <MenuLink item={item} pathname={pathname} />
            </SidebarMenuItem>
          ))}
        </SidebarMenu>
      </nav>
    </SidebarGroup>
  )
}
