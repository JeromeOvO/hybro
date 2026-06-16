"use client"

import { Button } from "@/components/ui/button"
import { ArrowRight } from "lucide-react"
import { useClerk } from "@clerk/nextjs"
import { isWaitlistEnabled } from "@/lib/utils"

export function AboutCtaButton() {
  const { openWaitlist } = useClerk()
  return (
    <Button
      variant="outline"
      size="lg"
      className="px-8"
      onClick={() => {
        if (isWaitlistEnabled()) {
          openWaitlist()
        } else {
          window.location.href = "/sign-in"
        }
      }}
    >
      {isWaitlistEnabled() ? "Join Waitlist" : "Sign in"}
      <ArrowRight className="ml-2 h-4 w-4 icon-action" />
    </Button>
  )
}
