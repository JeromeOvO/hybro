import * as React from "react"
import { Slot } from "@radix-ui/react-slot"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

const badgeVariants = cva(
  "inline-flex items-center justify-center rounded-md border px-2 py-0.5 text-xs font-medium w-fit whitespace-nowrap shrink-0 [&>svg]:size-3 gap-1 [&>svg]:pointer-events-none focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px] aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 aria-invalid:border-destructive transition-[color,box-shadow] overflow-hidden",
  {
    variants: {
      variant: {
        default:
          "border-transparent bg-primary text-primary-foreground [a&]:hover:bg-primary/90",
        secondary:
          "border-transparent bg-secondary text-secondary-foreground [a&]:hover:bg-secondary/90",
        destructive:
          "border-transparent bg-destructive text-white [a&]:hover:bg-destructive/90 focus-visible:ring-destructive/20 dark:focus-visible:ring-destructive/40 dark:bg-destructive/60",
        outline:
          "text-foreground [a&]:hover:bg-accent [a&]:hover:text-accent-foreground",
        success:
          "bg-[rgb(240,253,244)] text-[rgb(21,128,61)] border-[rgb(187,247,208)] dark:bg-[rgb(20,83,45,0.3)] dark:text-[rgb(134,239,172)] dark:border-[rgb(22,101,52)]",
        successInteractive:
          "bg-[rgb(240,253,244)] text-[rgb(22,163,74)] border-[rgb(134,239,172)] hover:bg-[rgb(220,252,231)] dark:bg-[rgb(4,47,46)] dark:text-[rgb(74,222,128)] dark:border-[rgb(21,128,61)] dark:hover:bg-[rgb(20,83,45)]",
        error:
          "bg-[rgb(254,242,242)] text-[rgb(220,38,38)] border-[rgb(252,165,165)] dark:bg-[rgb(69,10,10)] dark:text-[rgb(248,113,113)] dark:border-[rgb(185,28,28)]",
        inactive:
          "bg-[rgb(249,250,251)] text-[rgb(55,65,81)] border-[rgb(229,231,235)] dark:bg-[rgb(31,41,55,0.5)] dark:text-[rgb(156,163,175)] dark:border-[rgb(55,65,81)]",
        badgeMuted:
          "text-muted-foreground border-muted bg-muted/50 hover:bg-muted/70",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

function Badge({
  className,
  variant,
  asChild = false,
  ...props
}: React.ComponentProps<"span"> &
  VariantProps<typeof badgeVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot : "span"

  return (
    <Comp
      data-slot="badge"
      className={cn(badgeVariants({ variant }), className)}
      {...props}
    />
  )
}

export { Badge, badgeVariants }
