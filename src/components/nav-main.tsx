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

  const getIconClass = (title: string) =>
    title === "History"
      ? "text-foreground dark:icon-navigation"
      : "icon-navigation"

  const isSubItemActive = (url: string) => {
    if (url === "#") return false
    return pathname === url || pathname.startsWith(url + "/")
  }

  return (
    <SidebarGroup>
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
                        "transition-all duration-200 ease-out",
                        "group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:px-0",
                        "group-data-[collapsible=icon]:h-12! group-data-[collapsible=icon]:w-full!",
                        "hover:bg-sidebar-accent",
                        "active:scale-[0.98]",
                        hasActiveChild && "bg-sidebar-accent font-medium"
                      )}
                    >
                      {item.icon && (
                        <div className={cn(
                          "relative flex items-center justify-center",
                          "group-data-[collapsible=icon]:mx-auto"
                        )}>
                          <item.icon className={cn(
                            getIconClass(item.title),
                            "transition-transform duration-200",
                            "group-hover/collapsible:scale-110"
                          )} />
                          {hasActiveChild && state === "collapsed" && (
                            <span className="absolute -top-0.5 -right-0.5 h-2 w-2 rounded-full bg-primary animate-pulse" />
                          )}
                        </div>
                      )}
                      <span className="group-data-[collapsible=icon]:hidden truncate">
                        {item.title}
                      </span>
                      {item.isLoading ? (
                        <RefreshCw className="ml-auto h-4 w-4 animate-spin icon-action group-data-[collapsible=icon]:hidden" />
                      ) : (
                        <ChevronRight className={cn(
                          "ml-auto h-4 w-4 shrink-0 icon-neutral",
                          "opacity-0 transition-opacity duration-200",
                          "group-hover/menu-item:opacity-100 group-data-[state=open]/collapsible:opacity-100",
                          "transition-transform duration-300 ease-out",
                          "group-data-[state=open]/collapsible:rotate-90",
                          "group-data-[collapsible=icon]:hidden"
                        )} />
                      )}
                    </SidebarMenuButton>
                  </CollapsibleTrigger>
                </div>
                <CollapsibleContent className="animate-in slide-in-from-top-1 duration-200">
                  <SidebarMenuSub className="gap-0.5 mt-1 ml-3 border-l border-sidebar-border/50 pl-0">
                    {item.items?.map((subItem, index) => {
                      const isActive = isSubItemActive(subItem.url)

                      return (
                        <SidebarMenuSubItem key={subItem.id || subItem.title || index}>
                          <SidebarMenuSubButton
                            asChild={subItem.url !== "#"}
                            className={cn(
                              "h-9 text-[0.9rem] pl-4 relative",
                              "transition-all duration-200 ease-out",
                              "hover:bg-sidebar-accent",
                              "before:absolute before:left-0 before:top-1/2 before:-translate-y-1/2",
                              "before:h-1.5 before:w-1.5 before:rounded-full",
                              "before:transition-all before:duration-200",
                              isActive
                                ? "bg-sidebar-accent font-medium text-sidebar-primary before:bg-primary before:scale-100"
                                : "before:bg-sidebar-border before:scale-75 hover:before:scale-100 hover:before:bg-sidebar-primary/50"
                            )}
                          >
                            {subItem.url !== "#" ? (
                              <Link href={subItem.url} prefetch={false} scroll={false}>
                                <span className="group-data-[collapsible=icon]:hidden truncate">
                                  {subItem.title}
                                </span>
                              </Link>
                            ) : (
                              <span className="text-muted-foreground group-data-[collapsible=icon]:hidden truncate">
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
