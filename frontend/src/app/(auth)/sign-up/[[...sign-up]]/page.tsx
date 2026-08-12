import { SignUp } from '@/lib/auth'

export default async function Page ({
  searchParams,
}: {
  searchParams: Promise<{ redirect_url?: string }>
}) {
  const { redirect_url } = await searchParams
  const redirectUrl = redirect_url || '/'

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
