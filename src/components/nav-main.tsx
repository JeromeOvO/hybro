"use client"

import { ChevronRight, RefreshCw, type LucideIcon } from "lucide-react"

import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import {
  SidebarGroup,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
} from "@/components/ui/sidebar"
import { Button } from "@/components/ui/button"

export function NavMain({
  items,
  onRefreshSessions,
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
      id?: string // Add optional id field
    }[]
  }[]
  onRefreshSessions?: () => void
}) {
  return (
    <SidebarGroup>
      <div className="flex items-center justify-between px-2">
      <SidebarGroupLabel>Playground</SidebarGroupLabel>
        {onRefreshSessions && (
          <Button
            variant="ghost"
            size="sm"
            onClick={onRefreshSessions}
            className="h-6 w-6 p-0 opacity-60 hover:opacity-100"
          >
            <RefreshCw className="h-3 w-3" />
          </Button>
        )}
      </div>
      <SidebarMenu>
        {items.map((item) => (
          <Collapsible
            key={item.title}
            asChild
            defaultOpen={item.isActive}
            className="group/collapsible"
          >
            <SidebarMenuItem>
              <CollapsibleTrigger asChild>
                <SidebarMenuButton tooltip={item.title}>
                  {item.icon && <item.icon />}
                  <span>{item.title}</span>
                  {item.isLoading ? (
                    <RefreshCw className="ml-auto h-4 w-4 animate-spin" />
                  ) : (
                  <ChevronRight className="ml-auto transition-transform duration-200 group-data-[state=open]/collapsible:rotate-90" />
                  )}
                </SidebarMenuButton>
              </CollapsibleTrigger>
              <CollapsibleContent>
                <SidebarMenuSub>
                  {item.items?.map((subItem, index) => (
                    <SidebarMenuSubItem key={subItem.id || subItem.title || index}>
                      <SidebarMenuSubButton asChild={subItem.url !== "#"}>
                        {subItem.url !== "#" ? (
                        <a href={subItem.url}>
                          <span>{subItem.title}</span>
                        </a>
                        ) : (
                          <span className="text-muted-foreground">{subItem.title}</span>
                        )}
                      </SidebarMenuSubButton>
                    </SidebarMenuSubItem>
                  ))}
                </SidebarMenuSub>
              </CollapsibleContent>
            </SidebarMenuItem>
          </Collapsible>
        ))}
      </SidebarMenu>
    </SidebarGroup>
  )
}
