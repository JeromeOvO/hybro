"use client"

import { useState, useEffect, useCallback, useRef } from "react"
import { Loader2, Monitor, Smartphone, Globe, Shield } from "lucide-react"
import type { UserResource, SessionWithActivitiesResource } from "@clerk/types"
import { useSession } from "@clerk/nextjs"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"

import { LoadingButton } from "@/components/settings/loading-button"

const SESSION_ROW_CLASS = "flex items-center gap-3 rounded-lg border p-3"

function formatRelativeTime(date: Date): string {
  const now = Date.now()
  const diffMs = now - date.getTime()
  const diffSeconds = Math.floor(diffMs / 1000)
  const diffMinutes = Math.floor(diffSeconds / 60)
  const diffHours = Math.floor(diffMinutes / 60)
  const diffDays = Math.floor(diffHours / 24)

  if (diffSeconds < 60) return "Just now"
  if (diffMinutes < 60) return `${diffMinutes}m ago`
  if (diffHours < 24) return `${diffHours}h ago`
  if (diffDays < 30) return `${diffDays}d ago`
  return date.toLocaleDateString()
}

function getDeviceIcon(deviceType?: string) {
  if (!deviceType) return <Globe className="h-5 w-5 icon-neutral" />
  const dt = deviceType.toLowerCase()
  if (dt.includes("mobile") || dt.includes("phone") || dt.includes("tablet")) {
    return <Smartphone className="h-5 w-5 icon-neutral" />
  }
  return <Monitor className="h-5 w-5 icon-neutral" />
}

export function SessionsSection({ user }: { user: UserResource }) {
  const { session: currentSession } = useSession()
  const [sessions, setSessions] = useState<SessionWithActivitiesResource[]>([])
  const [loading, setLoading] = useState(true)
  const [revokingId, setRevokingId] = useState<string | null>(null)
  const [revokingAll, setRevokingAll] = useState(false)
  const isInitialLoad = useRef(true)

  const loadSessions = useCallback(async () => {
    try {
      setLoading(true)
      // Only reload user on subsequent fetches (after revoke) to bust the cache.
      // Skip on initial mount to avoid an unnecessary API call.
      if (!isInitialLoad.current) {
        await user.reload()
      }
      isInitialLoad.current = false
      const result = await user.getSessions()
      // Only show active sessions
      setSessions(result.filter((s) => s.status === "active"))
    } catch {
      toast.error("Failed to load sessions")
    } finally {
      setLoading(false)
    }
  }, [user])

  useEffect(() => {
    loadSessions()
  }, [loadSessions])

  async function handleRevoke(session: SessionWithActivitiesResource) {
    try {
      setRevokingId(session.id)
      await session.revoke()
      // Refresh list after revoke (will call user.reload() to bust cache)
      await loadSessions()
      toast.success("Session revoked")
    } catch {
      toast.error("Failed to revoke session")
    } finally {
      setRevokingId(null)
    }
  }

  async function handleRevokeAll() {
    const others = sessions.filter((s) => s.id !== currentSession?.id)
    if (others.length === 0) return

    try {
      setRevokingAll(true)
      const results = await Promise.allSettled(others.map((s) => s.revoke()))
      const failed = results.filter((r) => r.status === "rejected").length
      const succeeded = results.filter((r) => r.status === "fulfilled").length

      // Refresh list regardless — some may have succeeded
      await loadSessions()

      if (failed === 0) {
        toast.success(`Revoked ${succeeded} session${succeeded > 1 ? "s" : ""}`)
      } else if (succeeded === 0) {
        toast.error("Failed to revoke sessions")
      } else {
        toast.warning(`Revoked ${succeeded} session${succeeded > 1 ? "s" : ""}, ${failed} failed`)
      }
    } catch {
      toast.error("Failed to revoke sessions")
    } finally {
      setRevokingAll(false)
    }
  }

  const otherSessions = sessions.filter((s) => s.id !== currentSession?.id)

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Shield className="h-5 w-5 icon-action" />
          Active sessions
        </CardTitle>
        <CardDescription>
          Manage your active sessions across devices
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {loading ? (
          <div className="space-y-3">
            {[1, 2].map((i) => (
              <div key={i} className={SESSION_ROW_CLASS}>
                <Skeleton className="h-10 w-10 rounded-full" />
                <div className="flex-1 space-y-2">
                  <Skeleton className="h-4 w-32" />
                  <Skeleton className="h-3 w-24" />
                </div>
              </div>
            ))}
          </div>
        ) : (
          <>
            {sessions.map((session) => {
              const activity = session.latestActivity
              const isCurrent = session.id === currentSession?.id
              const browserName = activity?.browserName ?? "Unknown browser"
              const deviceType = activity?.deviceType
              const ipAddress = activity?.ipAddress ?? "Unknown IP"
              const lastActive = session.lastActiveAt
                ? formatRelativeTime(new Date(session.lastActiveAt))
                : "Unknown"

              return (
                <div key={session.id} className={SESSION_ROW_CLASS}>
                  <div className="flex h-10 w-10 items-center justify-center rounded-full bg-muted text-muted-foreground shrink-0">
                    {getDeviceIcon(deviceType)}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium truncate">
                        {browserName}
                        {activity?.browserVersion ? ` ${activity.browserVersion}` : ""}
                      </span>
                      {isCurrent && (
                        <span className="shrink-0 rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
                          Current
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-muted-foreground truncate">
                      {ipAddress} &middot; {lastActive}
                    </p>
                  </div>
                  {!isCurrent && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="shrink-0 text-destructive hover:text-destructive hover:bg-destructive/10"
                      onClick={() => handleRevoke(session)}
                      disabled={revokingId === session.id || revokingAll}
                    >
                      {revokingId === session.id ? (
                        <Loader2 className="h-4 w-4 animate-spin icon-action" />
                      ) : (
                        "Revoke"
                      )}
                    </Button>
                  )}
                </div>
              )
            })}

            {otherSessions.length > 1 && (
              <div className="flex justify-end pt-2">
                <LoadingButton
                  variant="outline"
                  size="sm"
                  className="text-destructive hover:text-destructive"
                  loading={revokingAll}
                  onClick={handleRevokeAll}
                >
                  Sign out all other sessions
                </LoadingButton>
              </div>
            )}

            {sessions.length === 0 && (
              <p className="text-sm text-muted-foreground text-center py-4">
                No active sessions found.
              </p>
            )}
          </>
        )}
      </CardContent>
    </Card>
  )
}
