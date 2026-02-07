"use client"

import { useState } from "react"
import Image from "next/image"
import { Play } from "lucide-react"

interface VideoEmbedProps {
  /** YouTube video ID (e.g. "ZUQrnlBSsLg") */
  videoId: string
  /** Title for the video (used as iframe title and aria-label) */
  title?: string
  /** Optional className for the container */
  className?: string
}

/**
 * Lazy-loading YouTube video embed.
 * Shows a thumbnail with a play button. Loads the iframe only when clicked.
 * This avoids the performance hit of loading YouTube's iframe on page load.
 */
export function VideoEmbed({ videoId, title = "Video", className = "" }: VideoEmbedProps) {
  const [isPlaying, setIsPlaying] = useState(false)

  const thumbnailUrl = `https://img.youtube.com/vi/${videoId}/maxresdefault.jpg`

  if (isPlaying) {
    return (
      <div className={`relative w-full aspect-video rounded-lg overflow-hidden ${className}`}>
        <iframe
          src={`https://www.youtube.com/embed/${videoId}?autoplay=1&rel=0`}
          title={title}
          allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
          allowFullScreen
          className="absolute inset-0 w-full h-full"
        />
      </div>
    )
  }

  return (
    <button
      onClick={() => setIsPlaying(true)}
      className={`relative w-full aspect-video rounded-lg overflow-hidden group cursor-pointer bg-muted ${className}`}
      aria-label={`Play ${title}`}
    >
      {/* Thumbnail */}
      <Image
        src={thumbnailUrl}
        alt={title}
        fill
        className="absolute inset-0 object-cover transition-transform duration-300 group-hover:scale-105"
        loading="lazy"
        unoptimized
      />

      {/* Dark overlay */}
      <div className="absolute inset-0 bg-black/30 group-hover:bg-black/40 transition-colors" />

      {/* Play button */}
      <div className="absolute inset-0 flex items-center justify-center">
        <div className="flex items-center justify-center w-16 h-16 rounded-full bg-white/90 shadow-lg group-hover:bg-white group-hover:scale-110 transition-all duration-200">
          <Play className="h-7 w-7 text-foreground ml-1" fill="currentColor" />
        </div>
      </div>
    </button>
  )
}
