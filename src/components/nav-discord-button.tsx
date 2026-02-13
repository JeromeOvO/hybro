"use client"

import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"
import { DiscordIcon } from "@/components/icons"

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
          className="group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:px-0 group-data-[collapsible=icon]:h-12! group-data-[collapsible=icon]:w-full!"
        >
          <DiscordIcon className="h-4 w-4 group-data-[collapsible=icon]:mx-auto" />
          <span className="group-data-[collapsible=icon]:hidden">
            Join Community
          </span>
        </SidebarMenuButton>
      </SidebarMenuItem>
    </SidebarMenu>
  )
}