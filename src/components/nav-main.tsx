"use client"

import { ChevronRight, RefreshCw, type LucideIcon } from "lucide-react"
import Link from "next/link"

import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import {
  SidebarGroup,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
  useSidebar,
} from "@/components/ui/sidebar"
import { Button } from "@/components/ui/button"

export function NavMain({
  items,
  onRefreshSessions,
  onRefreshRooms,
}: {
  items: {
    title: string
    url: string
    icon?: LucideIcon
    isActive?: boolean
    isLoading?: boolean
    items?: {
      title: string
      url: string
      id?: string
    }[]
  }[]
  onRefreshSessions?: () => void
  onRefreshRooms?: () => void
}) {
  const { state } = useSidebar()
  const getRefreshHandler = (itemTitle: string) => {
    if (itemTitle === "Chat Sessions" && onRefreshSessions) {
      return onRefreshSessions
    }
    if (itemTitle === "History" && onRefreshRooms) {
      return onRefreshRooms
    }
    return undefined
  }

  return (
    <SidebarGroup>
      <SidebarMenu className="gap-1.5">
        {items.map((item) => {
          if (item.title === "History" && state === "collapsed") {
            return null
          }

          const refreshHandler = getRefreshHandler(item.title)
          
          return (
            <Collapsible
              key={item.title}
              asChild
              defaultOpen={item.isActive}
              className="group/collapsible"
            >
              <SidebarMenuItem>
                <div className="flex items-center w-full">
                  <CollapsibleTrigger asChild className="flex-1">
                    <SidebarMenuButton
                      tooltip={item.title}
                      size="lg"
                      className="text-base group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:px-0"
                    >
                      {item.icon && (
                        <item.icon className="icon-navigation group-data-[collapsible=icon]:mx-auto" />
                      )}
                      <span className="group-data-[collapsible=icon]:hidden">
                        {item.title}
                      </span>
                      {item.isLoading ? (
                        <RefreshCw className="ml-auto h-4 w-4 animate-spin icon-action group-data-[collapsible=icon]:hidden" />
                      ) : (
                        <ChevronRight className="ml-auto transition-transform duration-200 group-data-[state=open]/collapsible:rotate-90 icon-neutral group-data-[collapsible=icon]:hidden" />
                      )}
                    </SidebarMenuButton>
                  </CollapsibleTrigger>
                  {refreshHandler && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={refreshHandler}
                      className="h-6 w-6 p-0 opacity-60 hover:opacity-100 ml-1 flex-shrink-0 group-data-[collapsible=icon]:hidden"
                    >
                      <RefreshCw className="h-3 w-3 icon-action" />
                    </Button>
                  )}
                </div>
                <CollapsibleContent>
                  <SidebarMenuSub className="gap-1.5">
                    {item.items?.map((subItem, index) => (
                      <SidebarMenuSubItem key={subItem.id || subItem.title || index}>
                        <SidebarMenuSubButton asChild={subItem.url !== "#"} className="h-8 text-[0.95rem]">
                          {subItem.url !== "#" ? (
                            <Link href={subItem.url} prefetch={false} scroll={false}>
                              <span className="group-data-[collapsible=icon]:hidden">
                                {subItem.title}
                              </span>
                            </Link>
                          ) : (
                            <span className="text-muted-foreground group-data-[collapsible=icon]:hidden">
                              {subItem.title}
                            </span>
                          )}
                        </SidebarMenuSubButton>
                      </SidebarMenuSubItem>
                    ))}
                  </SidebarMenuSub>
                </CollapsibleContent>
              </SidebarMenuItem>
            </Collapsible>
          )
        })}
      </SidebarMenu>
    </SidebarGroup>
  )
}
