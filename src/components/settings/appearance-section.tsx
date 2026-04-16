"use client"

import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { SettingsCard } from "@/components/settings/settings-card"
import { useRoomUiStore } from "@/stores/room-ui-store"

export function AppearanceSection() {
  const turnBased = useRoomUiStore(s => s.globalTurnBasedTimeline)
  const setTurnBased = useRoomUiStore(s => s.setGlobalTurnBasedTimeline)

  return (
    <SettingsCard title="Appearance" description="Customize how messages are displayed">
      <div className="flex items-center justify-between rounded-lg border p-3">
        <div className="space-y-0.5">
          <Label htmlFor="turnBasedTimeline" className="text-sm font-medium">
            Block Message UI
          </Label>
          <p className="text-xs text-muted-foreground">
            Use turn-based block layout instead of classic chat bubbles
          </p>
        </div>
        <Switch
          id="turnBasedTimeline"
          checked={turnBased}
          onCheckedChange={setTurnBased}
        />
      </div>
    </SettingsCard>
  )
}
