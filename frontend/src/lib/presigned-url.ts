/**
 * Parse AWS SigV4 presigned URL params to check if the URL has expired.
 * Returns true if the URL is detectably expired, false otherwise
 * (including for non-presigned URLs where we can't determine expiry).
 */
export function isPresignedUrlExpired(url: string): boolean {
  try {
    const params = new URL(url).searchParams
    const date = params.get('X-Amz-Date')
    const expires = params.get('X-Amz-Expires')
    if (!date || !expires) return false
    const issued = Date.UTC(
      +date.slice(0, 4), +date.slice(4, 6) - 1, +date.slice(6, 8),
      +date.slice(9, 11), +date.slice(11, 13), +date.slice(13, 15),
    )
    if (Number.isNaN(issued)) return false
    return Date.now() > issued + (+expires * 1000)
  } catch {
    return false
  }
}
