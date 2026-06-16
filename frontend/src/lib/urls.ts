/**
 * Cross-subdomain URL helpers.
 *
 * hybro.ai           → consumer experience
 * developer.hybro.ai → developer experience
 *
 * In local development:
 *   localhost:3000       → consumer
 *   dev.localhost:3000   → developer
 */

const CONSUMER_ORIGIN =
  process.env.NEXT_PUBLIC_CONSUMER_URL || "http://localhost:3000"

const DEVELOPER_ORIGIN =
  process.env.NEXT_PUBLIC_DEVELOPER_URL || "http://dev.localhost:3000"

/** Build an absolute URL on the **consumer** subdomain (hybro.ai). */
export function consumerUrl(path = "/"): string {
  const normalized = path.startsWith("/") ? path : `/${path}`
  return `${CONSUMER_ORIGIN}${normalized}`
}

/** Build an absolute URL on the **developer** subdomain (developer.hybro.ai). */
export function developerUrl(path = "/"): string {
  const normalized = path.startsWith("/") ? path : `/${path}`
  return `${DEVELOPER_ORIGIN}${normalized}`
}
