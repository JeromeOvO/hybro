import { SignUp, Waitlist } from '@/lib/auth'
import { isWaitlistEnabled } from '@/lib/utils'

export default async function Page ({
  searchParams,
}: {
  searchParams: Promise<{ redirect_url?: string }>
}) {
  const { redirect_url } = await searchParams
  const waitlistEnabled = isWaitlistEnabled()
  const redirectUrl = redirect_url || '/'

  if (!waitlistEnabled) {
    return (
      <div className="flex flex-col items-center justify-center gap-4">
        <SignUp
          forceRedirectUrl={redirectUrl}
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
