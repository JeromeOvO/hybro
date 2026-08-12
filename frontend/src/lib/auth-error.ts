/** Return a user-facing message for failures from the local auth adapter. */
export function getAuthErrorMessage(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback
}
