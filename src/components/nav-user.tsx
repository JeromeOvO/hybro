"use client"

import { useState, useEffect } from "react"
import { UserPlus, LogOut, Settings, Sun, Moon, Monitor } from "lucide-react"
import { useUser, useClerk } from "@clerk/nextjs"
import { useTheme } from "next-themes"
import { ThemeToggle } from "@/components/theme-toggle"
import { isWaitlistEnabled } from "@/lib/utils"
import { SIDEBAR_ICON_BUTTON, SIDEBAR_ICON_CENTER, SIDEBAR_ICON_HIDDEN } from "@/lib/sidebar-styles"
import { Avatar, AvatarImage, AvatarFallback } from "@/components/ui/avatar"
import { useSettingsDialog } from "@/components/settings/settings-dialog-provider"
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubTrigger,
  DropdownMenuSubContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
} from "@/components/ui/dropdown-menu"
import * as DropdownMenuPrimitive from "@radix-ui/react-dropdown-menu"
import { cn } from "@/lib/utils"

import {
  SidebarMenu,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar"

/**
 * Dropdown menu content that renders without a portal on mobile
 * (to stay inside the Sheet content tree and avoid Radix Dialog
 * modal pointer-event conflicts), and with the standard portal
 * on desktop.
 */
/** Opaque dropdown background — removes backdrop-blur and uses the secondary token. */
const DROPDOWN_BG = "backdrop-blur-none bg-secondary"

function UserDropdownContent({ children }: { children: React.ReactNode }) {
  const { isMobile } = useSidebar()

  const sharedClasses = cn(
    "bg-popover text-popover-foreground",
    "data-[state=open]:animate-in data-[state=closed]:animate-out",
    "data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0",
    "data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95",
    "data-[side=top]:slide-in-from-bottom-2",
    "z-50 min-w-48 w-64 overflow-hidden rounded-md border p-1 shadow-md",
    "origin-(--radix-dropdown-menu-content-transform-origin)",
  )

  if (isMobile) {
    // Non-portaled: stays inside the Sheet content tree
    return (
      <DropdownMenuPrimitive.Content
        data-slot="dropdown-menu-content"
        side="top"
        align="start"
        sideOffset={8}
        className={cn(sharedClasses, DROPDOWN_BG)}
      >
        {children}
      </DropdownMenuPrimitive.Content>
    )
  }

  // Desktop: standard portaled dropdown
  return (
    <DropdownMenuContent
      side="top"
      align="start"
      sideOffset={8}
      className={cn("w-64", DROPDOWN_BG)}
    >
      {children}
    </DropdownMenuContent>
  )
}

export function NavUser() {
  const { user, isLoaded } = useUser()
  const { openWaitlist, signOut } = useClerk()
  const { setOpenMobile } = useSidebar()
  const { openSettings } = useSettingsDialog()
  const { theme, setTheme } = useTheme()
  const [hydrated, setHydrated] = useState(false)
  const [themeSubOpen, setThemeSubOpen] = useState(false)

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
                  window.location.href = `/sign-in?redirect_url=${encodeURIComponent(window.location.pathname + window.location.search)}`
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
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              className={`flex h-10 w-full items-center gap-2 px-2 hover:bg-black/10 dark:hover:bg-white/15 hover:text-sidebar-accent-foreground rounded-md transition-all duration-150 ease-out focus:outline-none focus:ring-2 focus:ring-sidebar-ring ${SIDEBAR_ICON_BUTTON}`}
              title={userEmail ? `${userName} (${userEmail})` : userName}
            >
              <div className={`shrink-0 ${SIDEBAR_ICON_CENTER}`}>
                <Avatar className="h-8 w-8 rounded-lg">
                  <AvatarImage src={user.imageUrl} alt={userName} />
                  <AvatarFallback className="rounded-lg text-xs">
                    {userName.charAt(0).toUpperCase()}
                  </AvatarFallback>
                </Avatar>
              </div>
              <div className={`grid flex-1 text-left text-sm leading-tight ${SIDEBAR_ICON_HIDDEN}`}>
                <span className="truncate font-medium">{userName}</span>
                <span className="truncate text-xs text-muted-foreground">{userEmail}</span>
              </div>
            </button>
          </DropdownMenuTrigger>
          <UserDropdownContent>
            <DropdownMenuLabel className="px-2 py-1.5">
              <div className="text-sm font-medium">{userName}</div>
              <div className="text-xs text-muted-foreground font-normal">{userEmail}</div>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={() => {
              openSettings()
              setOpenMobile(false)
            }}>
              <Settings className="mr-2 h-4 w-4" />
              Settings
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuSub open={themeSubOpen} onOpenChange={setThemeSubOpen}>
              <DropdownMenuSubTrigger>
                {theme === "dark" ? (
                  <Moon className="mr-2 h-4 w-4" />
                ) : theme === "light" ? (
                  <Sun className="mr-2 h-4 w-4" />
                ) : (
                  <Monitor className="mr-2 h-4 w-4" />
                )}
                Theme
              </DropdownMenuSubTrigger>
              <DropdownMenuSubContent>
                <DropdownMenuRadioGroup value={theme} onValueChange={(val) => {
                  setTheme(val)
                  setThemeSubOpen(false)
                }}>
                  <DropdownMenuRadioItem value="light" onSelect={(e) => e.preventDefault()}>
                    <Sun className="mr-2 h-4 w-4" /> Light
                  </DropdownMenuRadioItem>
                  <DropdownMenuRadioItem value="dark" onSelect={(e) => e.preventDefault()}>
                    <Moon className="mr-2 h-4 w-4" /> Dark
                  </DropdownMenuRadioItem>
                  <DropdownMenuRadioItem value="system" onSelect={(e) => e.preventDefault()}>
                    <Monitor className="mr-2 h-4 w-4" /> System
                  </DropdownMenuRadioItem>
                </DropdownMenuRadioGroup>
              </DropdownMenuSubContent>
            </DropdownMenuSub>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={() => signOut()}>
              <LogOut className="mr-2 h-4 w-4" />
              Sign out
            </DropdownMenuItem>
          </UserDropdownContent>
        </DropdownMenu>
      </SidebarMenuItem>
    </SidebarMenu>
  )
}
