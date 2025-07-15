"use client"

import { LogIn } from "lucide-react"
import { useClerk } from "@clerk/nextjs"

import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"

export function NavUserSignIn() {
  const { openSignIn } = useClerk()

  const handleSignIn = () => {
    openSignIn()
  }

  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <SidebarMenuButton size="lg" onClick={handleSignIn}>
          <LogIn className="h-4 w-4" />
          <span>Sign In</span>
        </SidebarMenuButton>
      </SidebarMenuItem>
    </SidebarMenu>
  )
} 