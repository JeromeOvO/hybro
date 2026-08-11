import { SignIn } from '@/lib/auth'
import Link from 'next/link'

export default async function Page ({
  searchParams,
}: {
  searchParams: Promise<{ redirect_url?: string }>
}) {
  const { redirect_url } = await searchParams
  const redirectUrl = redirect_url || '/'

  return (
    <div className="flex flex-col items-center justify-center gap-4">
      <SignIn
        forceRedirectUrl={redirectUrl}
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
            href={redirect_url ? `/sign-up?redirect_url=${encodeURIComponent(redirect_url)}` : "/sign-up"}
            className="text-primary hover:underline font-medium"
          >
            Create an account
          </Link>
        </p>
      </div>
    </div>
  )
}
