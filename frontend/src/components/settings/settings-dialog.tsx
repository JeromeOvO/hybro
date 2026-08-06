"use client"

import { useUser } from "@/lib/auth"
import { RefreshCw } from "lucide-react"

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
import { Separator } from "@/components/ui/separator"

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
