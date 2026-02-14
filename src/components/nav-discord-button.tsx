"use client"

import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"
import { DiscordIcon } from "@/components/icons"
import { SIDEBAR_ICON_BUTTON, SIDEBAR_ICON_CENTER, SIDEBAR_ICON_HIDDEN } from "@/lib/sidebar-styles"

export function DiscordButton() {
  const handleDiscordClick = () => {
    window.open('https://discord.gg/2S5pCKzUmJ', '_blank')
  }

  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <SidebarMenuButton
          size="default"
          onClick={handleDiscordClick}
          tooltip="Join Community"
          className={`${SIDEBAR_ICON_BUTTON} hover:text-[#5865F2] dark:hover:text-[#7289DA]`}
        >
          <DiscordIcon className={`h-4 w-4 transition-colors ${SIDEBAR_ICON_CENTER}`} />
          <span className={SIDEBAR_ICON_HIDDEN}>
            Join Community
          </span>
        </SidebarMenuButton>
      </SidebarMenuItem>
    </SidebarMenu>
  )
}
