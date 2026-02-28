"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import { useAuth } from "@clerk/nextjs"
import { Copy, KeyRound, Loader2, Plus, Trash2 } from "lucide-react"
import { toast } from "sonner"

import { createApiKey, deleteApiKey, listApiKeys } from "@/lib/api"
import type { APIKeyItemResponse } from "@/lib/types/response"
import { ApiError } from "@/lib/api-client"

import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"

function formatDate(dateValue: string | null | undefined): string {
  if (!dateValue) return "Never"
  const date = new Date(dateValue)
  if (Number.isNaN(date.getTime())) return "Unknown"
  return date.toLocaleString()
}

function getCreateErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 400) {
    return "Please provide a valid key name."
  }
  return "Failed to create API key."
}

function getDeleteErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 404) {
    return "The key no longer exists."
  }
  return "Failed to delete API key."
}

export default function DeveloperApiKeysPage() {
  const { getToken } = useAuth()
  const [keys, setKeys] = useState<APIKeyItemResponse[]>([])
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [deletingKeyId, setDeletingKeyId] = useState<string | null>(null)
  const [newKeyName, setNewKeyName] = useState("")
  const [createdKey, setCreatedKey] = useState<string | null>(null)
  const [createdKeyName, setCreatedKeyName] = useState<string>("")

  const activeCount = useMemo(
    () => keys.filter((key) => key.is_active).length,
    [keys]
  )

  const loadKeys = useCallback(async () => {
    try {
      setLoading(true)
      const response = await listApiKeys(getToken)
      setKeys(response.keys.filter((k) => k.is_active))
    } catch {
      toast.error("Failed to load API keys")
    } finally {
      setLoading(false)
    }
  }, [getToken])

  useEffect(() => {
    loadKeys()
  }, [loadKeys])

  async function handleCreateKey() {
    const name = newKeyName.trim()
    if (!name) {
      toast.error("Key name is required")
      return
    }

    try {
      setCreating(true)
      const response = await createApiKey({ name }, getToken)
      setCreatedKey(response.api_key)
      setCreatedKeyName(response.name)
      setNewKeyName("")
      await loadKeys()
      toast.success("API key created")
    } catch (error) {
      toast.error(getCreateErrorMessage(error))
    } finally {
      setCreating(false)
    }
  }

  async function handleDeleteKey(keyId: string) {
    try {
      setDeletingKeyId(keyId)
      await deleteApiKey(keyId, getToken)
      setKeys((prev) => prev.filter((k) => k.key_id !== keyId))
      toast.success("API key deleted")
    } catch (error) {
      toast.error(getDeleteErrorMessage(error))
    } finally {
      setDeletingKeyId(null)
    }
  }

  async function handleCopyCreatedKey() {
    if (!createdKey) return
    try {
      await navigator.clipboard.writeText(createdKey)
      toast.success("Copied API key")
    } catch {
      toast.error("Failed to copy API key")
    }
  }

  return (
    <div className="page-container">
      <div className="page-content space-y-6">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-2xl">
              <KeyRound className="h-6 w-6 text-icon-action" />
              API Key Management
            </CardTitle>
            <CardDescription>
              Create and manage API keys for Discovery API access.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-[1fr_auto]">
              <Input
                value={newKeyName}
                onChange={(event) => setNewKeyName(event.target.value)}
                placeholder="Key name (for example: Production)"
                maxLength={100}
              />
              <Button
                className="btn-brand-gradient"
                onClick={handleCreateKey}
                disabled={creating}
              >
                {creating ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <Plus className="mr-2 h-4 w-4" />
                )}
                Create key
              </Button>
            </div>
            
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Your API Keys</CardTitle>
          </CardHeader>
          <CardContent>
            {loading ? (
              <div className="flex items-center justify-center py-8 text-muted-foreground">
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Loading keys...
              </div>
            ) : keys.length === 0 ? (
              <div className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
                No API keys yet. Create your first key above.
              </div>
            ) : (
              <div className="rounded-lg border border-border/50 overflow-hidden">
                <table className="w-full">
                  <thead>
                    <tr className="border-b bg-muted/40 text-left text-xs uppercase tracking-wide text-muted-foreground">
                      <th className="px-4 py-3">Name</th>
                      <th className="px-4 py-3">Status</th>
                      <th className="px-4 py-3 hidden md:table-cell">Created</th>
                      <th className="px-4 py-3 hidden md:table-cell">Last used</th>
                      <th className="px-4 py-3 hidden sm:table-cell">Usage</th>
                      <th className="px-4 py-3 text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {keys.map((key) => (
                      <tr key={key.key_id} className="border-b last:border-0">
                        <td className="px-4 py-3">
                          <div className="font-medium">{key.name}</div>
                        </td>
                        <td className="px-4 py-3">
                          <Badge variant={key.is_active ? "success" : "inactive"}>
                            {key.is_active ? "Active" : "Inactive"}
                          </Badge>
                        </td>
                        <td className="px-4 py-3 hidden md:table-cell text-sm text-muted-foreground">
                          {formatDate(key.created_at)}
                        </td>
                        <td className="px-4 py-3 hidden md:table-cell text-sm text-muted-foreground">
                          {formatDate(key.last_used_at)}
                        </td>
                        <td className="px-4 py-3 hidden sm:table-cell">{key.usage_count}</td>
                        <td className="px-4 py-3 text-right">
                          <AlertDialog>
                            <AlertDialogTrigger asChild>
                              <Button
                                variant="ghost"
                                size="sm"
                                className="text-destructive hover:text-destructive"
                                disabled={!key.is_active || deletingKeyId === key.key_id}
                              >
                                {deletingKeyId === key.key_id ? (
                                  <Loader2 className="h-4 w-4 animate-spin" />
                                ) : (
                                  <Trash2 className="h-4 w-4" />
                                )}
                                <span className="ml-2">Delete</span>
                              </Button>
                            </AlertDialogTrigger>
                            <AlertDialogContent>
                              <AlertDialogHeader>
                                <AlertDialogTitle>Delete API key?</AlertDialogTitle>
                                <AlertDialogDescription>
                                  This will deactivate the key immediately. Existing integrations using this key will stop working.
                                </AlertDialogDescription>
                              </AlertDialogHeader>
                              <AlertDialogFooter>
                                <AlertDialogCancel>Cancel</AlertDialogCancel>
                                <AlertDialogAction
                                  className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                                  onClick={() => handleDeleteKey(key.key_id)}
                                >
                                  Delete key
                                </AlertDialogAction>
                              </AlertDialogFooter>
                            </AlertDialogContent>
                          </AlertDialog>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <Dialog
        open={Boolean(createdKey)}
        onOpenChange={(open) => {
          if (!open) {
            setCreatedKey(null)
            setCreatedKeyName("")
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Save your API key</DialogTitle>
            <DialogDescription>
            Your API key is displayed only once. Please copy and store it in a secure location immediately.
            </DialogDescription>
          </DialogHeader>
          <div className="rounded-md border bg-muted/40 p-3 font-mono text-sm break-all">
            {createdKey}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={handleCopyCreatedKey}>
              <Copy className="mr-2 h-4 w-4" />
              Copy key
            </Button>
            <Button
              onClick={() => {
                setCreatedKey(null)
                setCreatedKeyName("")
              }}
            >
              Done
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
