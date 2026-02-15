import { clsx, type ClassValue } from "clsx"
import { extendTailwindMerge } from "tailwind-merge"

/**
 * Custom color tokens from the design system (globals.css).
 * Registering them here teaches tailwind-merge that e.g. `bg-sidebar`
 * and `bg-background` belong to the same class group, so the last
 * one wins instead of both being kept in the output.
 */
const CUSTOM_COLORS = [
  "background", "foreground",
  "card", "card-foreground",
  "popover", "popover-foreground",
  "primary", "primary-foreground",
  "secondary", "secondary-foreground",
  "muted", "muted-foreground",
  "accent", "accent-foreground",
  "destructive", "destructive-foreground",
  "border", "input", "ring",
  "sidebar", "sidebar-foreground",
  "sidebar-primary", "sidebar-primary-foreground",
  "sidebar-accent", "sidebar-accent-foreground",
  "sidebar-border", "sidebar-ring",
]

const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      "bg-color": CUSTOM_COLORS.map((c) => `bg-${c}`),
      "text-color": CUSTOM_COLORS.map((c) => `text-${c}`),
      "border-color": CUSTOM_COLORS.map((c) => `border-${c}`),
      "ring-color": CUSTOM_COLORS.map((c) => `ring-${c}`),
    },
  },
})

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function getApiUrl(endpoint: string): string {
  const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000';
  const apiPrefix = process.env.NEXT_PUBLIC_API_PREFIX || '/api/v1';
  return `${baseUrl}${apiPrefix}/${endpoint}`;
}

// Waitlist configuration
export function isWaitlistEnabled(): boolean {
  return process.env.NEXT_PUBLIC_ENABLE_WAITLIST === 'true';
}

export function getInspectionTimeoutMs(): number {
  return parseInt(process.env.NEXT_PUBLIC_INSPECTION_TIMEOUT_MS || '300000');
}

/**
 * Detect raw JSON content and wrap it in a fenced code block so
 * ReactMarkdown renders it as a scrollable <pre> with syntax highlighting.
 * Non-JSON content passes through untouched.
 */
export function formatIfJson(text: string): string {
  const trimmed = text.trim()
  if (
    (trimmed.startsWith('{') && trimmed.endsWith('}')) ||
    (trimmed.startsWith('[') && trimmed.endsWith(']'))
  ) {
    try {
      const parsed = JSON.parse(trimmed)
      return '```json\n' + JSON.stringify(parsed, null, 2) + '\n```'
    } catch {
      // Not valid JSON, return as-is
    }
  }
  return text
}