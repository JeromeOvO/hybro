"use client"

import { useRef, useState } from "react"
import { Camera, Loader2 } from "lucide-react"
import { useAuth } from "@/lib/auth"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { banner } from "@/components/ui/banner"
import { getAgentAvatarUri } from "@/lib/agent-avatar"
import { uploadAgentAvatar } from "@/lib/api"
import { cn } from "@/lib/utils"

interface AgentAvatarUploadProps {
  agentId: string
  agentName: string
  iconUrl?: string | null
  onUploaded?: (newIconUrl: string) => void
  className?: string
}

const ACCEPTED_TYPES = "image/jpeg,image/png,image/webp,image/gif"

export function AgentAvatarUpload({
  agentId,
  agentName,
  iconUrl,
  onUploaded,
  className,
}: AgentAvatarUploadProps) {
  const { getToken } = useAuth()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const [localIconUrl, setLocalIconUrl] = useState<string | null | undefined>(iconUrl)

  const handleClick = () => {
    if (!uploading) fileInputRef.current?.click()
  }

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return

    // Reset input so the same file can be re-selected after an error
    e.target.value = ""

    setUploading(true)
    try {
      const result = await uploadAgentAvatar(agentId, file, getToken)
      setLocalIconUrl(result.iconUrl)
      onUploaded?.(result.iconUrl)
      banner.success("Avatar updated")
    } catch (err) {
      banner.error("Failed to upload avatar", {
        description: err instanceof Error ? err.message : "Please try again.",
      })
    } finally {
      setUploading(false)
    }
  }

  return (
    <div
      className={cn("relative group cursor-pointer select-none", className)}
      onClick={handleClick}
      role="button"
      aria-label="Change avatar"
      tabIndex={0}
      onKeyDown={(e) => e.key === "Enter" && handleClick()}
    >
      <Avatar className="h-16 w-16 border-2 border-background shadow-lg">
        <AvatarImage src={localIconUrl || undefined} alt={agentName} />
        <AvatarFallback className="bg-primary/5 text-primary p-0 overflow-hidden">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={getAgentAvatarUri(agentId)} alt={agentName} className="h-full w-full" />
        </AvatarFallback>
      </Avatar>

      {/* Hover / loading overlay */}
      <div
        className={cn(
          "absolute inset-0 rounded-full flex items-center justify-center transition-opacity",
          uploading
            ? "bg-black/50 opacity-100"
            : "bg-black/50 opacity-0 group-hover:opacity-100"
        )}
      >
        {uploading ? (
          <Loader2 className="h-5 w-5 text-white animate-spin" />
        ) : (
          <Camera className="h-5 w-5 text-white" />
        )}
      </div>

      <input
        ref={fileInputRef}
        type="file"
        accept={ACCEPTED_TYPES}
        className="hidden"
        onChange={handleFileChange}
        disabled={uploading}
      />
    </div>
  )
}
