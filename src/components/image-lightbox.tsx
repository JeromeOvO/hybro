'use client'

import { useCallback, useEffect, useState } from 'react'
import { Download, X, ZoomIn } from 'lucide-react'
import { cn } from '@/lib/utils'

interface ImageLightboxProps {
  src: string
  alt: string
  className?: string
  caption?: string
  onError?: () => void
}

export function ImageLightbox({ src, alt, className, caption, onError }: ImageLightboxProps) {
  const [open, setOpen] = useState(false)
  const [loadError, setLoadError] = useState(false)

  const close = useCallback(() => setOpen(false), [])

  useEffect(() => {
    if (!open) return
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') close()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, close])

  const handleDownload = useCallback(async (e: React.MouseEvent) => {
    e.stopPropagation()
    try {
      const res = await fetch(src)
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = alt || 'image'
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } catch {
      window.open(src, '_blank')
    }
  }, [src, alt])

  if (loadError) return null

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className={cn('group/img relative cursor-pointer rounded-md overflow-hidden', className)}
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={src}
          alt={alt}
          className="max-w-full max-h-80 rounded-md border border-border object-cover transition-[filter] duration-200 group-hover/img:brightness-90"
          loading="lazy"
          onError={() => { setLoadError(true); onError?.() }}
        />
        <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover/img:opacity-100 transition-opacity duration-200">
          <div className="rounded-full bg-black/60 p-2 backdrop-blur-sm">
            <ZoomIn className="h-5 w-5 text-white" />
          </div>
        </div>
      </button>

      {open && (
        <div
          className="fixed inset-0 z-100 flex items-center justify-center bg-black/80 backdrop-blur-sm animate-in fade-in duration-150"
          onClick={close}
          role="dialog"
          aria-modal="true"
          aria-label={alt}
        >
          <div className="absolute top-4 right-4 flex items-center gap-2 z-10">
            <button
              type="button"
              onClick={handleDownload}
              className="rounded-full bg-white/15 p-2.5 text-white backdrop-blur-sm transition-colors hover:bg-white/25"
              aria-label="Download image"
            >
              <Download className="h-5 w-5" />
            </button>
            <button
              type="button"
              onClick={close}
              className="rounded-full bg-white/15 p-2.5 text-white backdrop-blur-sm transition-colors hover:bg-white/25"
              aria-label="Close"
            >
              <X className="h-5 w-5" />
            </button>
          </div>

          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={src}
            alt={alt}
            className="max-h-[90vh] max-w-[90vw] rounded-lg object-contain shadow-2xl animate-in zoom-in-95 duration-200"
            onClick={(e) => e.stopPropagation()}
          />

          {caption && (
            <div
              className="absolute bottom-6 left-1/2 -translate-x-1/2 rounded-lg bg-black/60 px-4 py-2 text-sm text-white backdrop-blur-sm"
              onClick={(e) => e.stopPropagation()}
            >
              {caption}
            </div>
          )}
        </div>
      )}
    </>
  )
}
