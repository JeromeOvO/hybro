"use client"

import { BookOpen } from "lucide-react"
import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"
import { SIDEBAR_ICON_CENTER, SIDEBAR_ICON_HIDDEN } from "@/lib/sidebar-styles"

export function DocsButton() {
  const handleDocsClick = () => {
    window.open('https://docs.hybro.ai/', '_blank')
  }

  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <SidebarMenuButton
          size="default"
          onClick={handleDocsClick}
          tooltip="Documentation"
          className="hover:text-primary"
        >
          <BookOpen className={`h-4 w-4 transition-colors ${SIDEBAR_ICON_CENTER}`} />
          <span className={SIDEBAR_ICON_HIDDEN}>
            Documentation
          </span>
        </SidebarMenuButton>
      </SidebarMenuItem>
    </SidebarMenu>
  )
}
