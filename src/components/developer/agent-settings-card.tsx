"use client"

import { Settings, Globe, Lock } from "lucide-react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Separator } from "@/components/ui/separator"

export interface AgentSettingsValues {
  isPublic: boolean
  enableUserLimit: boolean
  userLimitValue: string
  enableSystemLimit: boolean
  systemLimitValue: string
}

interface AgentSettingsCardProps {
  values: AgentSettingsValues
  onChange: (values: AgentSettingsValues) => void
}

export function AgentSettingsCard({ values, onChange }: AgentSettingsCardProps) {
  const { isPublic, enableUserLimit, userLimitValue, enableSystemLimit, systemLimitValue } = values

  const update = (partial: Partial<AgentSettingsValues>) => {
    onChange({ ...values, ...partial })
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <Settings className="h-5 w-5" />
          <CardTitle>Agent Settings</CardTitle>
        </div>
        <CardDescription>
          Configure visibility and rate limits for your agent.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* Visibility Toggle */}
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <div className="space-y-0.5">
              <Label htmlFor="visibility-toggle" className="text-base font-medium flex items-center gap-2">
                {isPublic ? <Globe className="h-4 w-4 text-green-500" /> : <Lock className="h-4 w-4 text-yellow-500" />}
                Visibility
              </Label>
              <p className="text-sm text-muted-foreground">
                {isPublic 
                  ? "Public - Everyone can discover and use this agent"
                  : "Private - Only you can see and use this agent"
                }
              </p>
            </div>
            <Switch
              id="visibility-toggle"
              checked={isPublic}
              onCheckedChange={(checked) => update({ isPublic: checked })}
            />
          </div>
        </div>

        <Separator />

        {/* Per-User Rate Limit */}
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <div className="space-y-0.5">
              <Label htmlFor="user-limit-toggle" className="text-base font-medium">
                Per-User Limit
              </Label>
              <p className="text-sm text-muted-foreground">
                Maximum requests each user can make per hour
              </p>
            </div>
            <Switch
              id="user-limit-toggle"
              checked={enableUserLimit}
              onCheckedChange={(checked) => {
                update({
                  enableUserLimit: checked,
                  userLimitValue: checked ? userLimitValue : "",
                })
              }}
            />
          </div>
          {enableUserLimit && (
            <div className="flex items-center gap-3 pl-4">
              <Input
                type="number"
                min="1"
                value={userLimitValue}
                onChange={(e) => update({ userLimitValue: e.target.value })}
                placeholder="e.g., 10"
                className="w-32"
              />
              <span className="text-sm text-muted-foreground">requests per hour</span>
            </div>
          )}
        </div>

        <Separator />

        {/* System-Wide Rate Limit */}
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <div className="space-y-0.5">
              <Label htmlFor="system-limit-toggle" className="text-base font-medium">
                System-Wide Limit
              </Label>
              <p className="text-sm text-muted-foreground">
                Maximum total requests from all users per hour
              </p>
            </div>
            <Switch
              id="system-limit-toggle"
              checked={enableSystemLimit}
              onCheckedChange={(checked) => {
                update({
                  enableSystemLimit: checked,
                  systemLimitValue: checked ? systemLimitValue : "",
                })
              }}
            />
          </div>
          {enableSystemLimit && (
            <div className="flex items-center gap-3 pl-4">
              <Input
                type="number"
                min="1"
                value={systemLimitValue}
                onChange={(e) => update({ systemLimitValue: e.target.value })}
                placeholder="e.g., 100"
                className="w-32"
              />
              <span className="text-sm text-muted-foreground">requests per hour</span>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

/**
 * Validate agent settings values. Returns an error message string if invalid, or null if valid.
 */
export function validateAgentSettings(values: AgentSettingsValues): string | null {
  const { enableUserLimit, userLimitValue, enableSystemLimit, systemLimitValue } = values

  if (enableUserLimit) {
    if (!userLimitValue) {
      return "User rate limit is enabled but no value is set. Please enter a value or disable the limit."
    }
    const parsed = parseInt(userLimitValue, 10)
    if (isNaN(parsed) || parsed < 1) {
      return "Invalid user rate limit. Please enter a number greater than or equal to 1."
    }
  }

  if (enableSystemLimit) {
    if (!systemLimitValue) {
      return "System rate limit is enabled but no value is set. Please enter a value or disable the limit."
    }
    const parsed = parseInt(systemLimitValue, 10)
    if (isNaN(parsed) || parsed < 1) {
      return "Invalid system rate limit. Please enter a number greater than or equal to 1."
    }
  }

  return null
}

/**
 * Convert AgentSettingsValues to the shape expected by the updateAgent API.
 */
export function settingsToUpdatePayload(values: AgentSettingsValues) {
  return {
    rate_limit_per_user_per_hour: values.enableUserLimit && values.userLimitValue
      ? parseInt(values.userLimitValue, 10)
      : null,
    rate_limit_system_per_hour: values.enableSystemLimit && values.systemLimitValue
      ? parseInt(values.systemLimitValue, 10)
      : null,
    is_public: values.isPublic,
  }
}
