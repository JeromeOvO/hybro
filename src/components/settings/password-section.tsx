"use client"

import { useState } from "react"
import type { UserResource } from "@clerk/types"
import { toast } from "sonner"
import { getClerkErrorMessage } from "@/lib/clerk-error"

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Switch } from "@/components/ui/switch"
import { Label } from "@/components/ui/label"

import { PasswordInput } from "@/components/settings/password-input"
import { LoadingButton } from "@/components/settings/loading-button"
import { FormGroup } from "@/components/settings/form-group"

const MIN_PASSWORD_LENGTH = 8

export function PasswordSection({ user }: { user: UserResource }) {
  const hasPassword = user.passwordEnabled

  const [currentPassword, setCurrentPassword] = useState("")
  const [newPassword, setNewPassword] = useState("")
  const [confirmPassword, setConfirmPassword] = useState("")
  const [signOutOthers, setSignOutOthers] = useState(true)
  const [saving, setSaving] = useState(false)

  const passwordsMatch = newPassword === confirmPassword
  const isLongEnough = newPassword.length >= MIN_PASSWORD_LENGTH
  const canSubmit =
    newPassword.length > 0 &&
    confirmPassword.length > 0 &&
    passwordsMatch &&
    isLongEnough &&
    (hasPassword ? currentPassword.length > 0 : true)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!canSubmit) return

    try {
      setSaving(true)
      await user.updatePassword({
        ...(hasPassword ? { currentPassword } : {}),
        newPassword,
        signOutOfOtherSessions: signOutOthers,
      })
      // Reset form on success
      setCurrentPassword("")
      setNewPassword("")
      setConfirmPassword("")
      toast.success(hasPassword ? "Password changed" : "Password set successfully")
    } catch (err: unknown) {
      toast.error(getClerkErrorMessage(err, "Failed to update password"))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Password</CardTitle>
        <CardDescription>
          {hasPassword
            ? "Change your password"
            : "You signed in with a social provider. Set a password to also sign in with email."}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Current password (only if user already has one) */}
          {hasPassword && (
            <FormGroup id="currentPassword" label="Current password">
              <PasswordInput
                id="currentPassword"
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
                placeholder="Enter current password"
                autoComplete="current-password"
              />
            </FormGroup>
          )}

          {/* New password */}
          <FormGroup id="newPassword" label="New password">
            <PasswordInput
              id="newPassword"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              placeholder="At least 8 characters"
              autoComplete="new-password"
            />
            {newPassword.length > 0 && !isLongEnough && (
              <p className="text-xs text-destructive">
                Password must be at least {MIN_PASSWORD_LENGTH} characters
              </p>
            )}
          </FormGroup>

          {/* Confirm password */}
          <FormGroup id="confirmPassword" label="Confirm new password">
            <PasswordInput
              id="confirmPassword"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              placeholder="Re-enter new password"
              autoComplete="new-password"
            />
            {confirmPassword.length > 0 && !passwordsMatch && (
              <p className="text-xs text-destructive">Passwords do not match</p>
            )}
          </FormGroup>

          {/* Sign out other sessions toggle */}
          <div className="flex items-center justify-between rounded-lg border p-3">
            <div className="space-y-0.5">
              <Label htmlFor="signOutOthers" className="text-sm font-medium">
                Sign out of other sessions
              </Label>
              <p className="text-xs text-muted-foreground">
                Recommended after changing your password
              </p>
            </div>
            <Switch
              id="signOutOthers"
              checked={signOutOthers}
              onCheckedChange={setSignOutOthers}
            />
          </div>

          <div className="flex justify-end">
            <LoadingButton type="submit" disabled={!canSubmit} loading={saving}>
              {hasPassword ? "Change password" : "Set password"}
            </LoadingButton>
          </div>
        </form>
      </CardContent>
    </Card>
  )
}
