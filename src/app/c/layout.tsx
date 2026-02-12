import type React from "react"
import type { Metadata } from "next"
import "@/app/globals.css"
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar"
import { ConsumerSidebar } from "@/components/consumer/consumer-sidebar"
import { ConsumerHeader } from "@/components/consumer/consumer-header"
import { BannerHost } from "@/components/ui/banner"

export const metadata: Metadata = {
  title: "Hybro AI",
  description: "Chat with AI agents",
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
        <ConsumerSidebar />
        <SidebarInset>
          <link rel="icon" href="/favicon.svg" type="image/svg+xml" />
          <ConsumerHeader />
          <main className="flex flex-1 flex-col min-w-0">
            {children}
          </main>
        </SidebarInset>
      </SidebarProvider>
    </div>
  )
}
