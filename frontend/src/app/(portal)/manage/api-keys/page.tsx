'use client'

import { KeyRound, Terminal } from "lucide-react"

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"

export default function DeveloperApiKeysPage() {
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
              Create and manage API keys for programmatically access Hybro Agent Network.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="rounded-lg bg-muted/40 border border-border/50 p-4 flex items-start gap-3">
              <Terminal className="h-4 w-4 text-primary shrink-0 mt-0.5" />
              <p className="text-sm text-muted-foreground leading-relaxed">
                <strong className="text-foreground">Local Open Source Mode:</strong> API keys are not required for local deployments. You can connect your <span className="font-medium text-foreground">Hybro Hub</span> or other integrations without an API key. 
                <br /><br />
                Just run <code className="font-mono bg-muted px-1 py-0.5 rounded text-foreground">HYBRO_GATEWAY_URL=http://localhost:8000 hybro-hub start</code> to get started. All requests are authenticated automatically as the local developer.
              </p>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
