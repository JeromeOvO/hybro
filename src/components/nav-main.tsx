"use client"

import { ChevronRight, RefreshCw, type LucideIcon } from "lucide-react"
import Link from "next/link"
import { usePathname } from "next/navigation"

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
import { cn } from "@/lib/utils"
import { SIDEBAR_ICON_CENTER, SIDEBAR_ICON_HIDDEN } from "@/lib/sidebar-styles"

export function NavMain({
  items,
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
}) {
  const { state } = useSidebar()
  const pathname = usePathname()

  const getIconClass = (title: string) => {
    if (title === "History") return "text-foreground dark:text-icon-navigation"
    if (title === "My Agents") return "text-icon-network"
    return "text-icon-navigation"
  }

  const isSubItemActive = (url: string) => {
    if (url === "#") return false
    return pathname === url || pathname.startsWith(url + "/")
  }

  return (
    <SidebarGroup className="min-h-0 flex-1 overflow-auto">
      <SidebarMenu className="gap-2">
        {items.map((item) => {
          if (item.title === "History" && state === "collapsed") {
            return null
          }

          const hasActiveChild = item.items?.some(subItem => isSubItemActive(subItem.url))

          return (
            <Collapsible
              key={item.title}
              asChild
              defaultOpen={item.isActive || hasActiveChild}
              className="group/collapsible"
            >
              <SidebarMenuItem>
                <div className="flex items-center w-full">
                  <CollapsibleTrigger asChild className="flex-1">
                    <SidebarMenuButton
                      tooltip={item.title}
                      size="default"
                      className={cn(
                        // History section header doesn't need hover highlight
                        item.title !== "History" && "hover:bg-black/10 dark:hover:bg-white/15",
                        "active:scale-[0.98]",
                        hasActiveChild && item.title !== "History" && "bg-black/15 dark:bg-white/15 font-medium"
                      )}
                    >
                      {item.icon && (
                        <div className={cn(
                          "relative flex items-center justify-center",
                          SIDEBAR_ICON_CENTER
                        )}>
                          <item.icon className={cn(
                            getIconClass(item.title),
                            "transition-colors"
                          )} />
                          {hasActiveChild && state === "collapsed" && (
                            <span className="absolute -top-0.5 -right-0.5 h-2 w-2 rounded-full bg-primary animate-pulse" />
                          )}
                        </div>
                      )}
                      <span className={`${SIDEBAR_ICON_HIDDEN} truncate`}>
                        {item.title}
                      </span>
                      {item.isLoading ? (
                        <RefreshCw className={`ml-auto h-4 w-4 animate-spin text-icon-action ${SIDEBAR_ICON_HIDDEN}`} />
                      ) : (
                        <ChevronRight className={cn(
                          "ml-auto h-4 w-4 shrink-0 text-icon-neutral",
                          "opacity-0 transition-opacity duration-200",
                          "group-hover/menu-item:opacity-100 group-data-[state=open]/collapsible:opacity-100",
                          "transition-transform duration-300 ease-out",
                          "group-data-[state=open]/collapsible:rotate-90",
                          SIDEBAR_ICON_HIDDEN
                        )} />
                      )}
                    </SidebarMenuButton>
                  </CollapsibleTrigger>
                </div>
                <CollapsibleContent className="animate-in slide-in-from-top-1 duration-200">
                  <SidebarMenuSub className="gap-0.5 mt-1 ml-3 pl-0">
                    {item.items?.map((subItem, index) => {
                      const isActive = isSubItemActive(subItem.url)

                      return (
                        <SidebarMenuSubItem key={subItem.id || subItem.title || index}>
                        <SidebarMenuSubButton
                          asChild={subItem.url !== "#"}
                          className={cn(
                            "h-9 text-[0.9rem] pl-4 relative",
                            "transition-all duration-150 ease-out",
                            "hover:bg-black/10 dark:hover:bg-white/15",
                            isActive
                              ? "bg-black/15 dark:bg-white/15 font-medium text-sidebar-primary"
                              : ""
                          )}
                        >
                            {subItem.url !== "#" ? (
                              <Link href={subItem.url} prefetch={false} scroll={false}>
                                <span className={`${SIDEBAR_ICON_HIDDEN} truncate`}>
                                  {subItem.title}
                                </span>
                              </Link>
                            ) : (
                              <span className={`text-muted-foreground ${SIDEBAR_ICON_HIDDEN} truncate`}>
                                {subItem.title}
                              </span>
                            )}
                          </SidebarMenuSubButton>
                        </SidebarMenuSubItem>
                      )
                    })}
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
