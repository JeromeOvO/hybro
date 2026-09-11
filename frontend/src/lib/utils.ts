import { clsx, type ClassValue } from "clsx"
import { extendTailwindMerge } from "tailwind-merge"
import { publicConfig } from './config'

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
  const config = publicConfig();
  const baseUrl = config.api_base_url;
  const apiPrefix = config.api_prefix;
  return `${baseUrl}${apiPrefix}/${endpoint}`;
}

export function getInspectionTimeoutMs(): number {
  return publicConfig().inspection_timeout_ms;
}

/**
 * Try to parse text as JSON. Returns the parsed value if it looks like a
 * JSON object or array and parses successfully, otherwise null.
 */
export function tryParseJson(text: string): unknown | null {
  const trimmed = text.trim()
  if (
    (trimmed.startsWith('{') && trimmed.endsWith('}')) ||
    (trimmed.startsWith('[') && trimmed.endsWith(']'))
  ) {
    try {
      return JSON.parse(trimmed)
    } catch {
      // Not valid JSON
    }
  }
  return null
}

/**
 * Detect raw JSON content and wrap it in a fenced code block so
 * ReactMarkdown renders it as a scrollable <pre> with syntax highlighting.
 * Non-JSON content passes through untouched.
 */
export function formatIfJson(text: string): string {
  const parsed = tryParseJson(text)
  if (parsed !== null) {
    return '```json\n' + JSON.stringify(parsed, null, 2) + '\n```'
  }
  return text
}