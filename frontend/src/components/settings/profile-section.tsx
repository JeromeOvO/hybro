"use client"

import { useState, useRef } from "react"
import { Camera, Loader2 } from "lucide-react"
import type { ClerkUser as UserResource } from "@/lib/auth"
import { toast } from "sonner"
import { getClerkErrorMessage } from "@/lib/clerk-error"

import { Avatar, AvatarImage, AvatarFallback } from "@/components/ui/avatar"
import { Input } from "@/components/ui/input"

import { SettingsCard } from "@/components/settings/settings-card"
import { LoadingButton } from "@/components/settings/loading-button"
import { FormGroup } from "@/components/settings/form-group"

const ALLOWED_IMAGE_TYPES = ["image/jpeg", "image/png", "image/gif", "image/webp"]
const MAX_FILE_SIZE = 10 * 1024 * 1024 // 10 MB (Clerk limit)

export function ProfileSection({ user }: { user: UserResource }) {
  const [firstName, setFirstName] = useState(user.firstName ?? "")
  const [lastName, setLastName] = useState(user.lastName ?? "")
  const [username, setUsername] = useState(user.username ?? "")
  const [saving, setSaving] = useState(false)
  const [uploadingAvatar, setUploadingAvatar] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const hasUsername = user.username !== null
  const isDirty =
    firstName !== (user.firstName ?? "") ||
    lastName !== (user.lastName ?? "") ||
    username !== (user.username ?? "")

  const userName = user.fullName || user.firstName || user.username || "User"
  const userEmail = user.primaryEmailAddress?.emailAddress ?? ""

  async function handleAvatarChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return

    if (!ALLOWED_IMAGE_TYPES.includes(file.type)) {
      toast.error("Invalid file type. Please use JPEG, PNG, GIF, or WebP.")
      return
    }
    if (file.size > MAX_FILE_SIZE) {
      toast.error("File too large. Maximum size is 10 MB.")
      return
    }

    try {
      setUploadingAvatar(true)
      await user.setProfileImage({ file })
      await user.reload()
      toast.success("Avatar updated")
    } catch (err: unknown) {
      toast.error(getClerkErrorMessage(err, "Failed to upload avatar"))
    } finally {
      setUploadingAvatar(false)
      // Reset input so re-selecting the same file triggers onChange
      if (fileInputRef.current) fileInputRef.current.value = ""
    }
  }

  async function handleSave() {
    if (!isDirty) return

    try {
      setSaving(true)
      await user.update({ firstName, lastName, username: username || undefined })
      await user.reload()
      toast.success("Profile updated")
    } catch (err: unknown) {
      toast.error(getClerkErrorMessage(err, "Failed to update profile"))
    } finally {
      setSaving(false)
    }
  }

  return (
    <SettingsCard title="Profile" description="Manage your name and avatar">
        {/* Avatar */}
        <div className="flex items-center gap-4">
          <button
            type="button"
            className="relative group rounded-full focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
            onClick={() => fileInputRef.current?.click()}
            disabled={uploadingAvatar}
            aria-label="Change avatar"
          >
            <Avatar className="h-20 w-20">
              <AvatarImage src={user.imageUrl} alt={userName} />
              <AvatarFallback className="text-lg">
                {userName.charAt(0).toUpperCase()}
              </AvatarFallback>
            </Avatar>
            <div className="absolute inset-0 flex items-center justify-center rounded-full bg-black/50 opacity-0 group-hover:opacity-100 transition-opacity">
              {uploadingAvatar ? (
                <Loader2 className="h-5 w-5 text-white animate-spin" />
              ) : (
                <Camera className="h-5 w-5 text-white" />
              )}
            </div>
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept={ALLOWED_IMAGE_TYPES.join(",")}
            className="hidden"
            onChange={handleAvatarChange}
          />
          <div className="text-sm text-muted-foreground">
            Click to upload a new photo
          </div>
        </div>

        {/* Name fields */}
        <div className="grid gap-4 sm:grid-cols-2">
          <FormGroup id="firstName" label="First name">
            <Input
              id="firstName"
              value={firstName}
              onChange={(e) => setFirstName(e.target.value)}
              placeholder="First name"
            />
          </FormGroup>
          <FormGroup id="lastName" label="Last name">
            <Input
              id="lastName"
              value={lastName}
              onChange={(e) => setLastName(e.target.value)}
              placeholder="Last name"
            />
          </FormGroup>
        </div>

        {/* Email (read-only) */}
        {userEmail && (
          <FormGroup id="email" label="Email" hint="Email cannot be changed from this page.">
            <Input id="email" value={userEmail} disabled className="opacity-60" />
          </FormGroup>
        )}

        {/* Username */}
        <FormGroup
          id="username"
          label="Username"
          hint={hasUsername ? "Your unique username." : "Set a username for your account."}
        >
          <Input
            id="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="Enter a username"
            autoComplete="username"
          />
        </FormGroup>

        <div className="flex justify-end">
          <LoadingButton onClick={handleSave} disabled={!isDirty} loading={saving}>
            Save changes
          </LoadingButton>
        </div>
    </SettingsCard>
  )
}
