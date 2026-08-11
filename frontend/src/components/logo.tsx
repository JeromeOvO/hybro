import Link from "next/link"
import { cn } from "@/lib/utils"

interface LogoProps {
  className?: string
  size?: 'sm' | 'md' | 'lg'
  hideOnMobile?: boolean
}

export function Logo({ 
  className, 
  size = 'md', 
  hideOnMobile = false 
}: LogoProps) {
  const sizeClasses = {
    sm: 'text-lg',
    md: 'text-2xl',
    lg: 'text-3xl'
  }

  return (
    <div className={cn(
      "flex items-center",
      "group-data-[collapsible=icon]/sidebar-wrapper:hidden",
      hideOnMobile && "max-sm:hidden",
      className
    )}>
      <Link href="/" className="flex items-center gap-0.5">
        <span
          className={cn(
            "font-bold font-spaceGrotesk text-[hsl(var(--color-hybro-hy))]",
            sizeClasses[size]
          )}
        >
          HY
        </span>
        <span
          className={cn(
            "font-bold font-spaceGrotesk text-[hsl(var(--color-hybro-bro))]",
            sizeClasses[size]
          )}
        >
          BRO
        </span>
      </Link>
    </div>
  )
}