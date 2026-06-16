import { isClerkAPIResponseError } from "@clerk/nextjs/errors"

/**
 * Extract a user-friendly error message from a Clerk API error.
 *
 * Clerk throws `ClerkAPIResponseError` with an `.errors` array containing
 * `{ code, message, longMessage }` objects. This helper extracts the best
 * available message, falling back to a generic string.
 */
export function getClerkErrorMessage(err: unknown, fallback: string): string {
  if (isClerkAPIResponseError(err)) {
    const first = err.errors[0]
    return first?.longMessage || first?.message || fallback
  }
  if (err instanceof Error) {
    return err.message
  }
  return fallback
}
