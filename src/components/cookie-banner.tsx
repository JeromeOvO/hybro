"use client"

import { useEffect, useState } from "react"

const CONSENT_KEY = "cookie_consent"

export function CookieBanner() {
  const [show, setShow] = useState(false)

  useEffect(() => {
    const stored = localStorage.getItem(CONSENT_KEY)
    if (!stored) {
      setShow(true)
    } else if (stored === "accepted") {
      window.gtag?.("consent", "update", { analytics_storage: "granted" })
    }
  }, [])

  const accept = () => {
    localStorage.setItem(CONSENT_KEY, "accepted")
    setShow(false)
    window.gtag?.("consent", "update", { analytics_storage: "granted" })
  }

  const decline = () => {
    localStorage.setItem(CONSENT_KEY, "declined")
    setShow(false)
  }

  if (!show) return null

  return (
    <div className="fixed bottom-0 inset-x-0 z-50 border-t bg-background px-4 py-4 shadow-lg sm:flex sm:items-center sm:justify-between sm:gap-6">
      <p className="text-sm text-muted-foreground">
        We use cookies to analyze traffic via Google Analytics. See our{" "}
        <a href="/privacy" className="underline underline-offset-4 hover:text-foreground transition-colors">
          Privacy Policy
        </a>
        .
      </p>
      <div className="mt-3 flex shrink-0 gap-2 sm:mt-0">
        <button
          onClick={decline}
          className="rounded-md border px-4 py-1.5 text-sm hover:bg-muted transition-colors"
        >
          Decline
        </button>
        <button
          onClick={accept}
          className="rounded-md bg-primary px-4 py-1.5 text-sm text-primary-foreground hover:bg-primary/90 transition-colors"
        >
          Accept
        </button>
      </div>
    </div>
  )
}
