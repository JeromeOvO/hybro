import { publicConfig } from '@/lib/config'

/**
 * Serves the deployment's public projection to the browser.
 *
 * The projection is deployment configuration, not build input: one published
 * image serves every install, so the value is read per request. This route must
 * stay dynamic — a cached response would pin the first deployment's settings.
 */
export const dynamic = 'force-dynamic'

export function GET(): Response {
  const projection = JSON.stringify(publicConfig())
  return new Response(
    `window.__HYBRO_PUBLIC_CONFIG__ = ${JSON.stringify(projection)};\n`,
    {
      headers: {
        'Content-Type': 'application/javascript; charset=utf-8',
        'Cache-Control': 'no-store',
      },
    },
  )
}
