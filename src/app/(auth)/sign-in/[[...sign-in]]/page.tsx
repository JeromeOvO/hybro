import { SignIn } from '@clerk/nextjs'
import Link from 'next/link'

export default function Page () {
  return (
    <div className="flex flex-col items-center justify-center gap-4">
      <SignIn
        redirectUrl="/chat"
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
            Join the waitlist
          </Link>
        </p>
      </div>
    </div>
  )
}
