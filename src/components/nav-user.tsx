"use client"

import { LogIn } from "lucide-react"
import { useUser, UserButton, useClerk } from "@clerk/nextjs"
import { ThemeToggle } from "@/components/theme-toggle"

import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"

export function NavUser() {
  const { user, isLoaded } = useUser()
  const { openSignIn } = useClerk()

  // Show loading state while user data is being fetched
  if (!isLoaded) {
    return (
      <SidebarMenu>
        <SidebarMenuItem>
          <SidebarMenuButton size="lg" disabled>
            <div className="h-8 w-8 rounded-lg bg-muted animate-pulse" />
            <div className="grid flex-1 text-left text-sm leading-tight">
              <div className="h-4 w-20 bg-muted animate-pulse rounded" />
              <div className="h-3 w-16 bg-muted animate-pulse rounded mt-1" />
            </div>
          </SidebarMenuButton>
        </SidebarMenuItem>
      </SidebarMenu>
    )
  }

  // Show sign in button if user is not logged in
  if (!user) {
    return (
      <SidebarMenu>
        <SidebarMenuItem>
          <SidebarMenuButton size="lg" onClick={() => openSignIn()}>
            <LogIn className="h-4 w-4" />
            <span>Sign In</span>
          </SidebarMenuButton>
        </SidebarMenuItem>
      </SidebarMenu>
    )
  }

  const userName = user.fullName || user.firstName || user.username || "User"
  const userEmail = user.primaryEmailAddress?.emailAddress || ""

  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <div className="flex items-center gap-2 px-2 py-1.5 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground rounded-md transition-colors">
          <UserButton
            appearance={{
              elements: {
                avatarBox: "h-8 w-8 rounded-lg",
                userButtonPopoverCard: "shadow-lg border rounded-lg",
                userButtonPopoverActionButton: "hover:bg-muted transition-colors",
                userButtonPopoverActionButtonText: "text-sm",
                userButtonPopoverActionButtonIcon: "w-4 h-4",
              },
            }}
            showName={false}
          />
          <div className="grid flex-1 text-left text-sm leading-tight">
            <span className="truncate font-medium">{userName}</span>
            <span className="truncate text-xs text-muted-foreground">{userEmail}</span>
          </div>
          <ThemeToggle />
        </div>
      </SidebarMenuItem>
    </SidebarMenu>
  )
}
