import { SignIn } from '@clerk/nextjs'
import Link from 'next/link'
import { isWaitlistEnabled } from '@/lib/utils'

export default function Page ({
  searchParams,
}: {
  searchParams: { redirect_url?: string }
}) {
  const waitlistEnabled = isWaitlistEnabled()
  const redirectUrl = searchParams.redirect_url || '/'

  return (
    <div className="flex flex-col items-center justify-center gap-4">
      <SignIn
        redirectUrl={redirectUrl}
        appearance={{
          elements: {
            rootBox: "mx-auto",
            card: "shadow-lg",
          }
        }}
      />
      <div className="text-sm text-muted-foreground text-center">
        <p>Don&apos;t have an account?{' '}
          <Link 
            href="/sign-up"
            className="text-primary hover:underline font-medium"
          >
            {waitlistEnabled ? "Join the waitlist" : "Create an account"}
          </Link>
        </p>
      </div>
    </div>
  )
}
