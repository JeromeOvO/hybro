import { Waitlist } from '@clerk/nextjs'

export default function Page () {
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
