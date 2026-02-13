"use client"

import { useState, useEffect } from "react"
import { UserPlus } from "lucide-react"
import { useUser, UserButton, useClerk } from "@clerk/nextjs"
import { ThemeToggle } from "@/components/theme-toggle"
import { isWaitlistEnabled } from "@/lib/utils"
import { SIDEBAR_ICON_BUTTON, SIDEBAR_ICON_CENTER, SIDEBAR_ICON_HIDDEN } from "@/lib/sidebar-styles"

import {
  SidebarMenu,
  SidebarMenuItem,
} from "@/components/ui/sidebar"

export function NavUser() {
  const { user, isLoaded } = useUser()
  const { openWaitlist } = useClerk()
  const [hydrated, setHydrated] = useState(false)

  useEffect(() => {
    setHydrated(true)
  }, [])

  if (!hydrated || !isLoaded) {
    return (
      <SidebarMenu>
        <SidebarMenuItem>
          <div
            className={`flex h-10 items-center gap-2 px-2 rounded-md ${SIDEBAR_ICON_BUTTON}`}
          >
            <div className={`h-8 w-8 rounded-lg bg-muted animate-pulse shrink-0 ${SIDEBAR_ICON_CENTER}`} />
            <div className={`grid flex-1 text-left text-sm leading-tight ${SIDEBAR_ICON_HIDDEN}`}>
              <div className="h-4 w-20 bg-muted animate-pulse rounded" />
              <div className="h-3 w-16 bg-muted animate-pulse rounded mt-1" />
            </div>
          </div>
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
            className={`flex h-10 items-center gap-2 px-2 hover:bg-black/10 dark:hover:bg-white/15 hover:text-sidebar-accent-foreground rounded-md transition-all duration-150 ease-out ${SIDEBAR_ICON_BUTTON}`}
            title={isWaitlistEnabled() ? "Join Waitlist" : "Sign in"}
          >
            <div
              className={`flex items-center gap-2 flex-1 cursor-pointer ${SIDEBAR_ICON_CENTER}`}
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
              <div className={`grid flex-1 text-left text-sm leading-tight ${SIDEBAR_ICON_HIDDEN}`}>
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
            <div className={SIDEBAR_ICON_HIDDEN}>
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
          className={`flex h-10 items-center gap-2 px-2 hover:bg-black/10 dark:hover:bg-white/15 hover:text-sidebar-accent-foreground rounded-md transition-all duration-150 ease-out ${SIDEBAR_ICON_BUTTON}`}
          title={userEmail ? `${userName} (${userEmail})` : userName}
        >
          <div className={`shrink-0 ${SIDEBAR_ICON_CENTER}`}>
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
          </div>
          <div className={`grid flex-1 text-left text-sm leading-tight ${SIDEBAR_ICON_HIDDEN}`}>
            <span className="truncate font-medium">{userName}</span>
            <span className="truncate text-xs text-muted-foreground">{userEmail}</span>
          </div>
          <div className={SIDEBAR_ICON_HIDDEN}>
            <ThemeToggle />
          </div>
        </div>
      </SidebarMenuItem>
    </SidebarMenu>
  )
}
