import type React from "react"
import type { Metadata } from "next"
import { Plus_Jakarta_Sans, Space_Grotesk } from "next/font/google"
import Script from "next/script"
import "./globals.css"
import "streamdown/styles.css"
import { ThemeProvider } from "@/components/theme-provider"
import { ClerkProvider } from "@/lib/auth"
import { ClerkAuthProvider } from "@/components/providers/ClerkAuthProvider"
import { QueryProvider } from "@/components/providers/query-provider"
import { Toaster } from "@/components/ui/sonner"
import { CookieBanner } from "@/components/cookie-banner"

declare global {
  interface Window {
    gtag?: (...args: unknown[]) => void
  }
}

const plusJakarta = Plus_Jakarta_Sans({
  subsets: ['latin'],
  variable: '--font-plus-jakarta',
  weight: ['400', '500', '600', '700'],
})
const spaceGrotesk = Space_Grotesk({ subsets: ["latin"], weight: ["700"], variable: "--font-space-grotesk" })

export const metadata: Metadata = {
  title: "Hybro AI",
  description:
    "Hybro AI is a hybrid AI agent platform that connects local and remote AI agents into an interoperable network. Build, discover, and orchestrate agents in a unified interface.",
  openGraph: {
    title: "Hybro AI – Hybrid AI Agent Platform",
    description:
      "Connect local and remote AI agents into an interoperable A2A network. Build, discover, and orchestrate agents in a unified interface.",
    url: "https://hybro.ai",
    siteName: "Hybro AI",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "Hybro AI – Hybrid AI Agent Platform",
    description:
      "Connect local and remote AI agents into an interoperable A2A network. Build, discover, and orchestrate agents in a unified interface.",
  },
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <Script
          id="json-ld"
          type="application/ld+json"
          dangerouslySetInnerHTML={{
            __html: JSON.stringify({
              "@context": "https://schema.org",
              "@type": "SoftwareApplication",
              name: "Hybro AI",
              description:
                "A hybrid AI agent platform for building and connecting interoperable local and remote AI agents via A2A Agent-to-Agent protocol.",
              applicationCategory: "DeveloperApplication",
              url: "https://hybro.ai",
              operatingSystem: "Web",
              offers: { "@type": "Offer", price: "0", priceCurrency: "USD" },
              creator: {
                "@type": "Organization",
                name: "Hybro AI",
                url: "https://hybro.ai",
                email: "info@hybro.ai",
              },
            }),
          }}
        />
        <Script id="gtag-consent-default" strategy="afterInteractive">
          {`
            window.dataLayer = window.dataLayer || [];
            function gtag(){dataLayer.push(arguments);}
            gtag('consent', 'default', { analytics_storage: 'denied' });
          `}
        </Script>
        <Script
          async
          src="https://www.googletagmanager.com/gtag/js?id=G-8CX7RWH8R5"
          strategy="afterInteractive"
        />
        <Script id="google-analytics" strategy="afterInteractive">
          {`
            gtag('js', new Date());
            gtag('config', 'G-8CX7RWH8R5');
          `}
        </Script>
      </head>
      <body className={`${plusJakarta.className} ${plusJakarta.variable} ${spaceGrotesk.variable}`}>
        <ClerkProvider>
          <ClerkAuthProvider>
            <ThemeProvider attribute="class" defaultTheme="dark" enableSystem disableTransitionOnChange>
              <QueryProvider>
                {children}
              </QueryProvider>
              <Toaster richColors closeButton />
              <CookieBanner />
            </ThemeProvider>
          </ClerkAuthProvider>
        </ClerkProvider>
      </body>
    </html>
  )
}
