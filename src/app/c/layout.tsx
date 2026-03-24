import type React from "react"
import type { Metadata } from "next"
import "@/app/globals.css"
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar"
import { ConsumerSidebar } from "@/components/consumer/consumer-sidebar"
import { ConsumerHeader } from "@/components/consumer/consumer-header"
import { BannerHost } from "@/components/ui/banner"
import { SettingsDialogProvider } from "@/components/settings/settings-dialog-provider"

export const metadata: Metadata = {
  title: "HYBRO AI – Your Local & Remote Hybrid Agent Platform",
  description:
    "Work with local and remote AI agents in a unified, private platform. Connect on-device or cloud-based agents and get things done.",
}

export default function ConsumerLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <div className="min-h-screen bg-background">
      <BannerHost />
      <SidebarProvider>
        <SettingsDialogProvider>
          <ConsumerSidebar />
          <SidebarInset>
            <link rel="icon" href="/favicon.svg" type="image/svg+xml" />
            <ConsumerHeader />
            <main className="flex flex-1 flex-col min-w-0">
              {children}
            </main>
          </SidebarInset>
        </SettingsDialogProvider>
      </SidebarProvider>
    </div>
  )
}
