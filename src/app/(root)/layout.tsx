import type React from "react"
import type { Metadata } from "next"
import "@/app/globals.css"
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar"
import { AppSidebar } from "@/components/app-sidebar"
import { Header } from "@/components/header"
import { Toaster } from "@/components/ui/sonner"

export const metadata: Metadata = {
  title: "Hybro AI",
  description: "An Open A2A Agent Network",
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <div className="min-h-screen bg-background">
      <SidebarProvider>
          <AppSidebar />
                  <SidebarInset>
          <link rel="icon" href="/favicon.svg" type="image/svg+xml" />
          <Header />
          <main className="flex flex-1 flex-col">
              {children}
          </main>
        </SidebarInset>
      </SidebarProvider>
      <Toaster />
    </div>
  )
}
