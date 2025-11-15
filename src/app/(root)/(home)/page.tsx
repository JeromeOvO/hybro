"use client"

import { useRouter } from "next/navigation"
import { useEffect } from "react"
import { useUser } from "@clerk/nextjs"

export default function Page() {
  const router = useRouter()
  const { isLoaded } = useUser()

  useEffect(() => {
    if (isLoaded) {
      router.push('/chat')
    }
  }, [router, isLoaded])

  // Show loading while checking auth status
  if (!isLoaded) {
    return (
      <div className="flex items-center justify-center h-screen">
        <div className="text-center">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary mx-auto mb-4"></div>
          <p className="text-muted-foreground">Loading...</p>
        </div>
      </div>
    )
  }

}
