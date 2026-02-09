import { SignUp, Waitlist } from '@clerk/nextjs'
import { isWaitlistEnabled } from '@/lib/utils'

export default function Page () {
  const waitlistEnabled = isWaitlistEnabled()

  if (!waitlistEnabled) {
    // When waitlist is disabled, render a standard sign-up form instead
    return (
      <div className="flex flex-col items-center justify-center gap-4">
        <SignUp
          redirectUrl="/"
          appearance={{
            elements: {
              rootBox: "mx-auto",
              card: "shadow-lg",
            }
          }}
        />
      </div>
    )
  }

  return (
    <div className="flex flex-col items-center justify-center gap-4">
      <Waitlist 
        appearance={{
          elements: {
            rootBox: "mx-auto",
            card: "shadow-lg",
          }
        }}
      />
      <p className="text-sm text-muted-foreground text-center max-w-md">
        Join our waitlist to get early access. We&apos;ll notify you when your account is ready!
      </p>
    </div>
  )
}
