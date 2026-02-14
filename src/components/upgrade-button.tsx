"use client"

import { Gem } from "lucide-react"
import { useRouter } from "next/navigation"

import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"
import { SIDEBAR_ICON_BUTTON, SIDEBAR_ICON_CENTER, SIDEBAR_ICON_HIDDEN } from "@/lib/sidebar-styles"

export function UpgradeButton() {
  const router = useRouter()

  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <SidebarMenuButton
          size="default"
          onClick={() => router.push("/pricing")}
          tooltip="Upgrade"
          className={SIDEBAR_ICON_BUTTON}
        >
          <Gem className={`h-4 w-4 text-sky-400 transition-colors ${SIDEBAR_ICON_CENTER}`} />
          <span className={SIDEBAR_ICON_HIDDEN}>
            Upgrade
          </span>
        </SidebarMenuButton>
      </SidebarMenuItem>
    </SidebarMenu>
  )
}
