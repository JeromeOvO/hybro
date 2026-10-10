'use client'

import * as React from 'react'
import Image from 'next/image'
import { usePathname } from 'next/navigation'
import Link from 'next/link'
import { MessageCirclePlus, PanelLeftIcon } from 'lucide-react'
import { useUser } from '@/lib/auth'
import { NavAgent } from '@/components/nav-agent'
import { ChatHistory } from '@/components/portal/chat-history'
import { NavUser } from '@/components/nav-user'
import { Logo } from '@/components/logo'
import { DiscordButton } from '@/components/nav-discord-button'
import { DocsButton } from '@/components/nav-docs-button'
import { CONSUMER_NAV } from '@/lib/consumer-nav'
import { routes } from '@/lib/routes'
import { SIDEBAR_ICON_HIDDEN } from '@/lib/sidebar-styles'
import { Button } from '@/components/ui/button'
import { Sidebar, SidebarContent, SidebarFooter, SidebarHeader, useSidebar } from '@/components/ui/sidebar'

const MARKETING_PAGES: string[] = [routes.home, routes.core, routes.about, routes.pricing, routes.agents]

export function PortalSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
  const { user, isLoaded, isSignedIn } = useUser()
  const { state, toggleSidebar, isMobile } = useSidebar()
  const pathname = usePathname()
  const isChatPage = pathname === routes.chat || pathname.startsWith('/room/')

  const hideSidebar = MARKETING_PAGES.includes(pathname) && (!isLoaded || !isSignedIn)
  if (hideSidebar) return null
  const isCollapsed = state === 'collapsed'
  const history = (
    <ChatHistory
      enabled={Boolean(isLoaded && isSignedIn && user?.id)}
      userId={user?.id ?? ''}
    />
  )

  return (
    <>
      <Sidebar collapsible="icon" {...props}>
        <SidebarHeader className="py-0">
          <div className="flex h-14 items-center gap-2 px-2 group-data-[collapsible=icon]:px-0 group-data-[collapsible=icon]:justify-center">
            <Logo className={`flex-1 ${SIDEBAR_ICON_HIDDEN}`} />
            <button
              type="button"
              onClick={toggleSidebar}
              className={`hidden md:flex h-8 w-8 items-center justify-center rounded-md hover:bg-black/10 dark:hover:bg-white/15 transition-all duration-150 ease-out leading-none group ${isCollapsed ? 'hover:cursor-e-resize' : 'hover:cursor-w-resize'}`}
              aria-label={isCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            >
              {isCollapsed ? (
                <div className="relative h-5 w-5">
                  <Image src="/favicon.svg" alt="Hybro" width={20} height={20} className="absolute left-1/2 top-1/2 h-5 w-5 -translate-x-1/2 -translate-y-1/2 transition-opacity duration-150 group-hover:opacity-0" />
                  <PanelLeftIcon className="absolute left-1/2 top-1/2 h-5 w-5 -translate-x-1/2 -translate-y-1/2 transition-opacity duration-150 opacity-0 group-hover:opacity-100" />
                </div>
              ) : <PanelLeftIcon className="h-5 w-5" />}
            </button>
          </div>
        </SidebarHeader>
        <SidebarContent>
          <NavAgent key={user?.id ?? ''} navAgents={CONSUMER_NAV} />
          {isMobile && isChatPage ? history : null}
        </SidebarContent>
        <SidebarFooter>
          <div className="border-t border-sidebar-border mx-2 mb-1" />
          <DocsButton />
          <DiscordButton />
          <NavUser />
        </SidebarFooter>
      </Sidebar>
      {!isMobile && isChatPage ? (
        <Sidebar
          collapsible="none"
          role="complementary"
          aria-label="Chat history"
          className="sticky top-[var(--banner-offset,0px)] hidden h-[calc(100dvh-var(--banner-offset,0px))] shrink-0 md:flex"
        >
          <SidebarContent className="gap-1 pt-4">
            <div className="px-2 pb-1">
              <Button asChild variant="outline" className="w-full justify-start">
                <Link href={routes.chat}>
                  <MessageCirclePlus data-icon="inline-start" />
                  New chat
                </Link>
              </Button>
            </div>
            {history}
          </SidebarContent>
        </Sidebar>
      ) : null}
    </>
  )
}
