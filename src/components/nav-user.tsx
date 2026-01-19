"use client"

import { UserPlus } from "lucide-react"
import { useUser, UserButton, useClerk } from "@clerk/nextjs"
import { ThemeToggle } from "@/components/theme-toggle"
import { isWaitlistEnabled } from "@/lib/utils"

import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"

export function NavUser() {
  const { user, isLoaded } = useUser()
  const { openWaitlist } = useClerk()

  // Show loading state while user data is being fetched
  if (!isLoaded) {
    return (
      <SidebarMenu>
        <SidebarMenuItem>
          <SidebarMenuButton
            size="lg"
            disabled
            className="group-data-[collapsible=icon]:h-12! group-data-[collapsible=icon]:w-full!"
          >
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

  // Show sign in / waitlist entry if user is not logged in
  if (!user) {
    return (
      <SidebarMenu>
        <SidebarMenuItem>
          <div
            className="flex items-center gap-2 px-2 py-1.5 hover:bg-white/10 dark:hover:bg-white/15 hover:text-sidebar-accent-foreground rounded-md transition-all duration-150 ease-out group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:px-0 group-data-[collapsible=icon]:h-12! group-data-[collapsible=icon]:w-full!"
            title={isWaitlistEnabled() ? "Join Waitlist" : "Sign in"}
          >
            <div
              className="flex items-center gap-2 flex-1 cursor-pointer"
              onClick={() => {
                if (isWaitlistEnabled()) {
                  openWaitlist()
                } else {
                  // When waitlist is disabled, redirect to regular sign-in
                  window.location.href = "/sign-in"
                }
              }}
            >
              <div
                className="flex h-8 w-8 items-center justify-center rounded-lg 
                          bg-gradient-to-br from-[hsl(var(--color-hybro-hy))] 
                          to-[hsl(var(--color-hybro-bro))] shadow-sm flex-shrink-0"
              >
                <UserPlus className="h-4 w-4 text-white" />
              </div>
              <div className="grid flex-1 text-left text-sm leading-tight group-data-[collapsible=icon]:hidden">
                <span
                  className="truncate font-medium bg-gradient-to-r 
                            from-[hsl(var(--color-hybro-hy))] 
                            to-[hsl(var(--color-hybro-bro))]
                            bg-clip-text text-transparent"
                >
                  {isWaitlistEnabled() ? "Join Waitlist" : "Sign in"}
                </span>
              </div>
            </div>
            <div className="group-data-[collapsible=icon]:hidden">
              <ThemeToggle />
            </div>
          </div>
        </SidebarMenuItem>
      </SidebarMenu>
    )
  }

  const userName = user.fullName || user.firstName || user.username || "User"
  const userEmail = user.primaryEmailAddress?.emailAddress || ""

  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <div
          className="flex items-center gap-2 px-2 py-1.5 hover:bg-white/10 dark:hover:bg-white/15 hover:text-sidebar-accent-foreground rounded-md transition-all duration-150 ease-out group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:px-0 group-data-[collapsible=icon]:!h-12 group-data-[collapsible=icon]:!w-full"
          title={userEmail ? `${userName} (${userEmail})` : userName}
        >
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
          <div className="grid flex-1 text-left text-sm leading-tight group-data-[collapsible=icon]:hidden">
            <span className="truncate font-medium">{userName}</span>
            <span className="truncate text-xs text-muted-foreground">{userEmail}</span>
          </div>
          <div className="group-data-[collapsible=icon]:hidden">
            <ThemeToggle />
          </div>
        </div>
      </SidebarMenuItem>
    </SidebarMenu>
  )
}
