"use client"

import { useState } from "react"
import { AlertTriangle } from "lucide-react"
import type { UserResource } from "@clerk/types"
import { useClerk } from "@clerk/nextjs"
import { toast } from "sonner"
import { getClerkErrorMessage } from "@/lib/clerk-error"

import { Input } from "@/components/ui/input"
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"

import { SettingsCard } from "@/components/settings/settings-card"
import { LoadingButton } from "@/components/settings/loading-button"
import { FormGroup } from "@/components/settings/form-group"

export function DangerZoneSection({ user }: { user: UserResource }) {
  const { signOut } = useClerk()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [confirmEmail, setConfirmEmail] = useState("")
  const [deleting, setDeleting] = useState(false)

  // Only render if self-deletion is enabled in Clerk Dashboard
  if (!user.deleteSelfEnabled) return null

  const userEmail = user.primaryEmailAddress?.emailAddress ?? ""
  const emailMatches = confirmEmail.toLowerCase() === userEmail.toLowerCase()

  async function handleDelete() {
    if (!emailMatches) return

    try {
      setDeleting(true)
      await user.delete()
      toast.success("Account deleted")
      // Sign out and redirect after deletion
      await signOut({ redirectUrl: "/" })
    } catch (err: unknown) {
      toast.error(getClerkErrorMessage(err, "Failed to delete account"))
      setDeleting(false)
    }
  }

  return (
    <SettingsCard
      title={
        <span className="flex items-center gap-2 text-destructive">
          <AlertTriangle className="h-5 w-5 text-icon-error" />
          Danger zone
        </span>
      }
      description="Irreversible and destructive actions"
      className="border-destructive/30 dark:border-destructive/50"
    >
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div>
            <p className="text-sm font-medium">Delete account</p>
            <p className="text-xs text-muted-foreground">
              Permanently delete your account and all associated data. This action cannot be undone.
            </p>
          </div>

          <AlertDialog open={dialogOpen} onOpenChange={(open) => { setDialogOpen(open); if (!open) setConfirmEmail("") }}>
            <AlertDialogTrigger asChild>
              <LoadingButton
                variant="destructive"
                size="sm"
                className="shrink-0"
              >
                Delete account
              </LoadingButton>
            </AlertDialogTrigger>

            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle className="text-destructive">Delete account</AlertDialogTitle>
                <AlertDialogDescription>
                  This action is permanent and cannot be undone. All your data will be deleted.
                </AlertDialogDescription>
              </AlertDialogHeader>

              <FormGroup
                id="confirmEmail"
                label={<>Type <span className="font-semibold">{userEmail}</span> to confirm</>}
              >
                <Input
                  id="confirmEmail"
                  value={confirmEmail}
                  onChange={(e) => setConfirmEmail(e.target.value)}
                  placeholder={userEmail}
                  autoComplete="off"
                />
              </FormGroup>

              <AlertDialogFooter>
                <AlertDialogCancel
                  disabled={deleting}
                  onClick={() => setConfirmEmail("")}
                >
                  Cancel
                </AlertDialogCancel>
                <LoadingButton
                  variant="destructive"
                  loading={deleting}
                  disabled={!emailMatches}
                  onClick={handleDelete}
                >
                  Delete my account
                </LoadingButton>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
    </SettingsCard>
  )
}
