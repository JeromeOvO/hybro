"use client"

import { useRef, useEffect, useState, type ReactNode } from "react"
import { cn } from "@/lib/utils"

/**
 * Reveal styles. `fade` is the original gentle lift; the rest are the
 * heavier, slower entrances used on the marketing sections.
 */
export type RevealVariant = "fade" | "rise" | "wipe" | "left" | "right"

const VARIANT_CLASS: Record<RevealVariant, string> = {
  fade: "animate-fade-up",
  rise: "animate-reveal-rise",
  wipe: "animate-reveal-wipe",
  left: "animate-reveal-left",
  right: "animate-reveal-right",
}

interface FadeInSectionProps {
  children: ReactNode
  className?: string
  delay?: number
  /** IntersectionObserver threshold (0-1). Default 0.1 */
  threshold?: number
  /** Which entrance to play. Default "fade" keeps existing callers unchanged. */
  variant?: RevealVariant
}

export function FadeInSection({
  children,
  className,
  delay = 0,
  threshold = 0.1,
  variant = "fade",
}: FadeInSectionProps) {
  const ref = useRef<HTMLDivElement>(null)
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    const el = ref.current
    if (!el) return

    if (typeof IntersectionObserver === "undefined") {
      setVisible(true)
      return
    }

    const obs = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setVisible(true)
          obs.unobserve(el)
        }
      },
      // Fire a little before the element is fully in view so the long
      // entrances have room to play out as the user scrolls in.
      { threshold, rootMargin: "0px 0px -8% 0px" }
    )
    obs.observe(el)
    return () => obs.disconnect()
  }, [threshold])

  return (
    <div
      ref={ref}
      className={cn(visible ? VARIANT_CLASS[variant] : "opacity-0", className)}
      style={delay ? { animationDelay: `${delay}ms` } : undefined}
    >
      {children}
    </div>
  )
}
