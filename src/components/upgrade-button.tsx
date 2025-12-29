"use client"

import { Gem } from "lucide-react"
import { useRouter } from "next/navigation"

import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"

export function UpgradeButton() {
  const router = useRouter()

  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <SidebarMenuButton
          size="lg"
          onClick={() => router.push("/price")}
          tooltip="Upgrade"
          className="
            group-data-[collapsible=icon]:justify-center
            group-data-[collapsible=icon]:px-0
            group-data-[collapsible=icon]:w-full!
          "
        >
          <Gem
            className="
              h-4 w-4
              text-sky-400
              group-data-[collapsible=icon]:mx-auto
            "
          />

          <span className="group-data-[collapsible=icon]:hidden">
            Upgrade
          </span>
        </SidebarMenuButton>
      </SidebarMenuItem>
    </SidebarMenu>
  )
}
