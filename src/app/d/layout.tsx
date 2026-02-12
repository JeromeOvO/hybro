import type React from "react"
import type { Metadata } from "next"
import "@/app/globals.css"
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar"
import { DeveloperSidebar } from "@/components/developer/developer-sidebar"
import { DeveloperHeader } from "@/components/developer/developer-header"
import { BannerHost } from "@/components/ui/banner"

export const metadata: Metadata = {
  title: "HYBRO Developers",
  description: "Build interoperable AI agents",
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
        <DeveloperSidebar />
        <SidebarInset>
          <link rel="icon" href="/favicon.svg" type="image/svg+xml" />
          <DeveloperHeader />
          <main className="flex flex-1 flex-col min-w-0">
            {children}
          </main>
        </SidebarInset>
      </SidebarProvider>
    </div>
  )
}
