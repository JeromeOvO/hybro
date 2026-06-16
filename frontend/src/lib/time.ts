/**
 * Timestamp utilities for consistent handling across the frontend.
 * Mirrors backend common/utils/time.py patterns.
 */

/**
 * Normalize a timestamp string to ISO 8601 format with UTC timezone.
 * Handles various input formats:
 * - ISO 8601 with/without timezone
 * - Space-separated datetime (e.g., "2024-01-15 10:30:00")
 * - Missing timezone (assumes UTC)
 *
 * @param value - The timestamp string to normalize
 * @param fallbackToNow - If true, returns current time for invalid/missing input (default: true)
 * @returns ISO 8601 string in UTC, or null if invalid and fallbackToNow is false
 */
export function normalizeTimestamp(
  value?: string | null,
  fallbackToNow: boolean = true
): string | null {
  if (!value) {
    return fallbackToNow ? new Date().toISOString() : null
  }

  const trimmed = value.trim()

  // Check if already has timezone info (Z, +HH:MM, -HH:MM)
  const hasZone = /[zZ]|[+-]\d{2}:?\d{2}$/.test(trimmed)

  // Ensure 'T' separator between date and time
  const withT = trimmed.includes('T') ? trimmed : trimmed.replace(' ', 'T')

  // Add UTC timezone if missing
  const candidate = hasZone ? withT : `${withT}Z`

  const parsed = new Date(candidate)

  if (Number.isNaN(parsed.getTime())) {
    return fallbackToNow ? new Date().toISOString() : null
  }

  return parsed.toISOString()
}

/**
 * Normalize a timestamp string, always returning a string (never null).
 * Convenience wrapper for cases where a fallback to current time is always desired.
 *
 * @param value - The timestamp string to normalize
 * @returns ISO 8601 string in UTC
 */
export function normalizeTimestampOrNow(value?: string | null): string {
  return normalizeTimestamp(value, true) as string
}

/**
 * Parse a timestamp string to a Date object with validation.
 * Uses normalizeTimestamp internally for consistent parsing.
 *
 * @param value - The timestamp string to parse
 * @returns Date object, or null if invalid
 */
export function parseTimestamp(value?: string | null): Date | null {
  const normalized = normalizeTimestamp(value, false)
  return normalized ? new Date(normalized) : null
}

/**
 * Get the current UTC time as an ISO 8601 string.
 * Equivalent to backend's utcnow().isoformat()
 */
export function utcNow(): string {
  return new Date().toISOString()
}

/**
 * Calculate elapsed time in seconds from a timestamp to now.
 * Returns 0 if timestamp is invalid or in the future.
 *
 * @param timestamp - The start timestamp
 * @returns Elapsed seconds, or 0 if invalid
 */
export function elapsedSeconds(timestamp?: string | null): number {
  const parsed = parseTimestamp(timestamp)
  if (!parsed) return 0

  const elapsed = Math.floor((Date.now() - parsed.getTime()) / 1000)
  return Math.max(0, elapsed) // Don't return negative for future timestamps
}

/**
 * Format elapsed time as human-readable string.
 *
 * @param seconds - Elapsed time in seconds
 * @returns Formatted string like "5s", "2m 30s", "1h 15m"
 */
export function formatElapsedTime(seconds: number): string {
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
}

/**
 * Check if a timestamp is stale (older than threshold).
 *
 * @param timestamp - The timestamp to check
 * @param thresholdMs - Staleness threshold in milliseconds (default: 10 minutes)
 * @returns true if timestamp is older than threshold, or if timestamp is invalid/missing
 */
export function isStale(
  timestamp?: string | null,
  thresholdMs: number = 10 * 60 * 1000
): boolean {
  const parsed = parseTimestamp(timestamp)
  if (!parsed) return true // Treat missing/invalid as stale

  return Date.now() - parsed.getTime() > thresholdMs
}

/**
 * Format a timestamp for display using locale-aware formatting.
 * Returns a fallback string if the timestamp is invalid.
 *
 * @param timestamp - The timestamp string to format
 * @param options - Intl.DateTimeFormat options
 * @param fallback - Fallback string for invalid timestamps (default: "Unknown")
 * @returns Formatted date string
 */
export function formatTimestamp(
  timestamp?: string | null,
  options: Intl.DateTimeFormatOptions = {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  },
  fallback: string = 'Unknown'
): string {
  const parsed = parseTimestamp(timestamp)
  if (!parsed) return fallback

  return parsed.toLocaleString('en-US', options)
}
