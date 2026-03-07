import {
  Type,
  Image as ImageIcon,
  Video,
  Music,
  Braces,
  FileText,
  SquareCode,
} from "lucide-react"
import type { LucideIcon } from "lucide-react"

export const MIME_ICON_MAP: [RegExp, LucideIcon][] = [
  [/^text\//, Type],
  [/^image\//, ImageIcon],
  [/^video\//, Video],
  [/^audio\//, Music],
  [/^application\/json$/, Braces],
  [/^application\/pdf$/, FileText],
]

export function getModeIcon(mime: string): LucideIcon {
  for (const [pattern, icon] of MIME_ICON_MAP) {
    if (pattern.test(mime)) return icon
  }
  return SquareCode
}

export function deduplicateIcons(modes: string[]): LucideIcon[] {
  const seen = new Set<LucideIcon>()
  const result: LucideIcon[] = []
  for (const mode of modes) {
    const icon = getModeIcon(mode)
    if (!seen.has(icon)) {
      seen.add(icon)
      result.push(icon)
    }
  }
  return result
}
