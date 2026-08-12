'use client'

import { Button } from "@/components/ui/button"
import { ArrowRight } from "lucide-react"
import { useRouter } from "next/navigation"

export function AboutCtaButton() {
  const router = useRouter()
  return (
    <Button
      variant="outline"
      size="lg"
      className="px-8"
      onClick={() => router.push("/sign-in")}
    >
      Sign in
      <ArrowRight className="ml-2 h-4 w-4 icon-action" />
    </Button>
  )
}
