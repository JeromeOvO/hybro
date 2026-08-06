'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useState } from 'react'
import {
  Bot,
  ChevronRight,
  SlidersHorizontal,
} from 'lucide-react'

import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import {
  SidebarGroup,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
} from '@/components/ui/sidebar'
import { routes } from '@/lib/routes'
import { SIDEBAR_ICON_CENTER, SIDEBAR_ICON_HIDDEN } from '@/lib/sidebar-styles'

const items = [
  { title: 'My Agents', url: routes.manage.agents, icon: Bot },
]

export function NavManage() {
  const pathname = usePathname()
  const isManageArea = pathname.startsWith(routes.manage.root)
  const [manualState, setManualState] = useState({
    pathname,
    isOpen: isManageArea,
  })
  const isOpen = manualState.pathname === pathname
    ? manualState.isOpen
    : isManageArea

  const isActive = (url: string) => {
    if (url === routes.manage.agents) {
      return (
        pathname === routes.manage.agents ||
        (pathname.startsWith(`${routes.manage.agents}/`) &&
          pathname !== routes.manage.register)
      )
    }
    return pathname === url || pathname.startsWith(`${url}/`)
  }

  return (
    <SidebarGroup className="py-0">
      <SidebarMenu>
        <Collapsible
          asChild
          open={isOpen}
          onOpenChange={(nextOpen) => setManualState({ pathname, isOpen: nextOpen })}
          className="group/collapsible"
        >
          <SidebarMenuItem>
            <CollapsibleTrigger asChild>
              <SidebarMenuButton
                aria-label="Manage"
                isActive={isManageArea}
                size="default"
                tooltip="Manage"
              >
                <SlidersHorizontal className={`text-icon-workflow ${SIDEBAR_ICON_CENTER}`} />
                <span className={`${SIDEBAR_ICON_HIDDEN} leading-7`}>Manage</span>
                <ChevronRight className={`ml-auto transition-transform duration-200 group-data-[state=open]/collapsible:rotate-90 ${SIDEBAR_ICON_HIDDEN}`} />
              </SidebarMenuButton>
            </CollapsibleTrigger>
            <CollapsibleContent>
              <SidebarMenuSub className="mt-1">
                {items.map((item) => {
                  const active = isActive(item.url)

                  return (
                    <SidebarMenuSubItem key={item.title}>
                      <SidebarMenuSubButton asChild isActive={active}>
                        <Link
                          aria-current={active ? 'page' : undefined}
                          href={item.url}
                          prefetch={false}
                          scroll={false}
                        >
                          <item.icon />
                          <span>{item.title}</span>
                        </Link>
                      </SidebarMenuSubButton>
                    </SidebarMenuSubItem>
                  )
                })}
              </SidebarMenuSub>
            </CollapsibleContent>
          </SidebarMenuItem>
        </Collapsible>
      </SidebarMenu>
    </SidebarGroup>
  )
}
