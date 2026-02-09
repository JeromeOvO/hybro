import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

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