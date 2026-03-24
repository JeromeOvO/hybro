import type React from "react"
import type { Metadata } from "next"
import "@/app/globals.css"
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar"
import { DeveloperSidebar } from "@/components/developer/developer-sidebar"
import { DeveloperHeader } from "@/components/developer/developer-header"
import { BannerHost } from "@/components/ui/banner"
import { SettingsDialogProvider } from "@/components/settings/settings-dialog-provider"

export const metadata: Metadata = {
  title: "HYBRO Developers – Build & Connect Interoperable AI Agents",
  description:
    "APIs and SDKs to build, connect, and deploy interoperable AI agents. Run agents locally or in the cloud and orchestrate them with HYBRO.",
}

export default function DeveloperLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <div className="min-h-screen bg-background">
      <BannerHost />
      <SidebarProvider>
        <SettingsDialogProvider>
          <DeveloperSidebar />
          <SidebarInset>
            <link rel="icon" href="/favicon.svg" type="image/svg+xml" />
            <DeveloperHeader />
            <main className="flex flex-1 flex-col min-w-0 px-8 sm:px-12">
              {children}
            </main>
          </SidebarInset>
        </SettingsDialogProvider>
      </SidebarProvider>
    </div>
  )
}
