"use client"

import type { JSX } from "react"
import { useEffect, useLayoutEffect, useRef } from "react"
import { create } from "zustand"
import { X, CheckCircle2, Info, AlertTriangle, AlertCircle } from "lucide-react"
import { cn } from "@/lib/utils"

type BannerType = "success" | "error" | "info" | "warning"

type Banner = {
  id: string
  title: string
  description?: string
  type: BannerType
  duration: number
}

type BannerInput = Omit<Banner, "id" | "duration"> & {
  id?: string
  duration?: number
}

type BannerOptions = {
  description?: string
  duration?: number
  id?: string
}

type BannerStore = {
  banners: Banner[]
  addBanner: (banner: BannerInput) => string
  removeBanner: (id: string) => void
}

const DEFAULT_DURATION = 6000

const useBannerStore = create<BannerStore>((set, get) => ({
  banners: [],
  addBanner: ({ id, duration, ...banner }) => {
    const bannerId = id ?? createId()
    const ttl = duration ?? DEFAULT_DURATION

    set((state) => ({
      banners: [...state.banners, { ...banner, id: bannerId, duration: ttl }],
    }))

    if (typeof window !== "undefined" && ttl > 0) {
      window.setTimeout(() => {
        get().removeBanner(bannerId)
      }, ttl)
    }

    return bannerId
  },
  removeBanner: (id) =>
    set((state) => ({
      banners: state.banners.filter((banner) => banner.id !== id),
    })),
}))

const createId = () => {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID()
  }
  return Math.random().toString(36).slice(2)
}

const normalizeContent = (value: unknown, options?: BannerOptions): Omit<BannerInput, "type"> => {
  if (typeof value === "string") {
    return { title: value, description: options?.description, duration: options?.duration, id: options?.id }
  }

  if (value instanceof Error) {
    return {
      title: value.message || "Something went wrong",
      description: options?.description,
      duration: options?.duration,
      id: options?.id,
    }
  }

  if (value && typeof value === "object" && "message" in value) {
    const payload = value as { message: unknown; description?: unknown }

    if (typeof payload.message === "string") {
      const description =
        options?.description ?? (typeof payload.description === "string" ? payload.description : undefined)

      return {
        title: payload.message,
        description,
        duration: options?.duration,
        id: options?.id,
      }
    }
  }

  return {
    title: value ? String(value) : "Something went wrong",
    description: options?.description,
    duration: options?.duration,
    id: options?.id,
  }
}

const showBanner = (type: BannerType, title: unknown, options?: BannerOptions) => {
  const content = normalizeContent(title, options)

  return useBannerStore.getState().addBanner({
    ...content,
    type,
  })
}

export const banner = {
  success: (title: unknown, options?: BannerOptions) => showBanner("success", title, options),
  error: (title: unknown, options?: BannerOptions) => showBanner("error", title, options),
  info: (title: unknown, options?: BannerOptions) => showBanner("info", title, options),
  warning: (title: unknown, options?: BannerOptions) => showBanner("warning", title, options),
  message: (title: unknown, options?: BannerOptions) => showBanner("info", title, options),
  dismiss: (id: string) => useBannerStore.getState().removeBanner(id),
}

export const toast = banner

const typeStyles: Record<BannerType, string> = {
  success:
    "border-green-200 bg-green-50 text-green-900 shadow-green-100/60 dark:border-green-900/60 dark:bg-green-900/40 dark:text-green-50",
  error:
    "border-red-200 bg-red-50 text-red-900 shadow-red-100/60 dark:border-red-900/60 dark:bg-red-900/40 dark:text-red-50",
  info:
    "border-sky-200 bg-sky-50 text-sky-900 shadow-sky-100/60 dark:border-sky-900/60 dark:bg-sky-900/40 dark:text-sky-50",
  warning:
    "border-amber-200 bg-amber-50 text-amber-900 shadow-amber-100/60 dark:border-amber-900/60 dark:bg-amber-900/40 dark:text-amber-50",
}

const typeIcon: Record<BannerType, JSX.Element> = {
  success: <CheckCircle2 className="h-5 w-5 text-green-600 dark:text-green-200" aria-hidden="true" />,
  error: <AlertCircle className="h-5 w-5 text-red-600 dark:text-red-200" aria-hidden="true" />,
  info: <Info className="h-5 w-5 text-sky-600 dark:text-sky-200" aria-hidden="true" />,
  warning: <AlertTriangle className="h-5 w-5 text-amber-600 dark:text-amber-200" aria-hidden="true" />,
}

export function BannerHost() {
  const banners = useBannerStore((state) => state.banners)
  const removeBanner = useBannerStore((state) => state.removeBanner)
  const MAX_VISIBLE = 3
  const visibleBanners = banners.slice(-MAX_VISIBLE)
  const containerRef = useRef<HTMLDivElement | null>(null)

  useLayoutEffect(() => {
    const el = containerRef.current
    const root = document.documentElement
    const height = el?.offsetHeight ?? 0
    root.style.setProperty("--banner-offset", `${height}px`)
  }, [visibleBanners.length])

  useEffect(() => {
    return () => {
      document.documentElement.style.removeProperty("--banner-offset")
    }
  }, [])

  if (visibleBanners.length === 0) return null

  return (
    <div ref={containerRef} className="pointer-events-none sticky top-0 inset-x-0 z-50 w-full">
      <div className="flex w-full flex-col gap-0 px-0 py-0">
        {visibleBanners.map((banner) => (
          <div
            key={banner.id}
            className={cn(
              "pointer-events-auto flex w-full items-start gap-3 border px-4 py-3 rounded-none shadow-none",
              "transition-all",
              typeStyles[banner.type]
            )}
            role="status"
            aria-live="polite"
          >
            <span className="mt-0.5 shrink-0">{typeIcon[banner.type]}</span>
            <div className="flex-1">
              <p className="text-sm font-medium leading-5">{banner.title}</p>
              {banner.description && (
                <p className="mt-1 text-sm text-muted-foreground leading-5">{banner.description}</p>
              )}
            </div>
            <button
              type="button"
              onClick={() => removeBanner(banner.id)}
              className="ml-2 inline-flex h-6 w-6 items-center justify-center rounded-md border border-transparent text-sm text-current transition hover:bg-black/5 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              aria-label="Dismiss"
            >
              <X className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}

