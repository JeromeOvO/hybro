import { parsePublicConfig, type PublicConfig } from './config-schema'

/**
 * The public projection the server delivered with this document.
 *
 * Values are resolved on first use rather than captured at module scope so that
 * nothing reads a snapshot the build froze in. `src/app/layout.tsx` loads the
 * projection before any application code runs; the route that serves it is
 * `src/app/hybro-runtime-config/route.ts`.
 */
declare global {
  interface Window {
    __HYBRO_PUBLIC_CONFIG__?: string
  }
}

let resolved: Readonly<PublicConfig> | null = null

/**
 * The validated public configuration for this deployment.
 *
 * The server reads the container's `HYBRO_FRONTEND_CONFIG`; the browser reads
 * what the server rendered into the document. Tests run without the document, so
 * they fall back to the environment.
 */
export function publicConfig(): Readonly<PublicConfig> {
  if (resolved === null) {
    const delivered =
      typeof window === 'undefined' ? undefined : window.__HYBRO_PUBLIC_CONFIG__
    resolved = parsePublicConfig(delivered ?? process.env.HYBRO_FRONTEND_CONFIG)
  }
  return resolved
}
