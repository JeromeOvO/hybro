"use client"

import { useUser } from "@/lib/auth"
import { usePathname } from "next/navigation"
import { RefreshCw, House, ArrowRight } from "lucide-react"
import Link from "next/link"

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog"
import { ProfileSection } from "@/components/settings/profile-section"
import { PasswordSection } from "@/components/settings/password-section"
import { SessionsSection } from "@/components/settings/sessions-section"
import { DangerZoneSection } from "@/components/settings/danger-zone-section"
import { SettingsCard } from "@/components/settings/settings-card"
import { Separator } from "@/components/ui/separator"
import { useHubStatus } from "@/hooks/useHubStatus"

function HubStatusLine({ onNavigate }: { onNavigate: () => void }) {
  const { hasHub, isOnline, isLoading } = useHubStatus()
  const pathname = usePathname()
  const hubPath = pathname.startsWith("/d") ? "/d/hub" : "/c/hub"

  return (
    <SettingsCard title="My Hub" description="Local agent hub">
      <Link
        href={hubPath}
        onClick={onNavigate}
        className="flex items-center justify-between group hover:bg-muted/50 -mx-2 -my-1 px-2 py-1 rounded-md transition-colors"
      >
        <div className="flex items-center gap-2 text-sm">
          {isLoading ? (
            <>
              <RefreshCw className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
              <span className="text-muted-foreground">Checking...</span>
            </>
          ) : hasHub ? (
            isOnline ? (
              <>
                <House className="h-3.5 w-3.5 text-emerald-500" />
                <span className="text-emerald-600 dark:text-emerald-400">Connected</span>
              </>
            ) : (
              <>
                <House className="h-3.5 w-3.5 text-amber-500" />
                <span className="text-amber-600 dark:text-amber-400">Offline</span>
              </>
            )
          ) : (
            <>
              <House className="h-3.5 w-3.5 text-muted-foreground/50" />
              <span className="text-muted-foreground">Not set up</span>
            </>
          )}
        </div>
        <ArrowRight className="h-3.5 w-3.5 text-muted-foreground group-hover:text-foreground transition-colors" />
      </Link>
    </SettingsCard>
  )
}

interface SettingsDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function SettingsDialog({ open, onOpenChange }: SettingsDialogProps) {
  const { user, isLoaded } = useUser()

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="sm:max-w-2xl max-h-[85vh] flex flex-col p-0 gap-0"
      >
        <DialogHeader className="px-6 pt-6 pb-0">
          <DialogTitle>Settings</DialogTitle>
          <DialogDescription>Manage your account</DialogDescription>
        </DialogHeader>

        {!isLoaded || !user ? (
          <div className="flex flex-col items-center justify-center gap-4 py-12">
            <RefreshCw className="h-8 w-8 animate-spin text-icon-action" />
            <span className="text-base font-medium text-muted-foreground">
              Loading settings...
            </span>
          </div>
        ) : (
          <div className="overflow-y-auto flex-1 px-6 pb-6">
            <div className="space-y-6">
              <Separator />
              <ProfileSection user={user} />
              <HubStatusLine onNavigate={() => onOpenChange(false)} />
              <PasswordSection user={user} />
              <SessionsSection user={user} />
              <DangerZoneSection user={user} />
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
