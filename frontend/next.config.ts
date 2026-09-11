import type { NextConfig } from "next";
import { parsePublicConfig } from './src/lib/config-schema'

// Server-side rewrites are compiled into the image, so routing needs a
// projection here. Browser settings are NOT build input: the server serves the
// deployment's projection per request (src/app/hybro-runtime-config/route.ts)
// and the layout loads it before application code. Publishing those values
// through `env` would inline them into every bundle — including the serving
// route — and pin one image to one deployment's settings.
const publicConfig = parsePublicConfig(process.env.HYBRO_FRONTEND_CONFIG)

const nextConfig: NextConfig = {
  experimental: {
    prefetchInlining: true,
  },
  compiler: {
    removeConsole: {
      exclude: ['error', 'warn'],
    },
  },
  async rewrites() {
    // Allows seamless API proxying without CORS issues.
    // Defaults to localhost for direct development, but uses the container name for docker-compose.
    const backendUrl = process.env.HYBRO_BACKEND_URL || 'http://127.0.0.1:8000';
    const configuredPrefix = publicConfig.api_prefix;
    const apiPrefix = `/${configuredPrefix.replace(/^\/+|\/+$/g, '')}`;
    return [
      {
        source: `${apiPrefix}/:path*`,
        destination: `${backendUrl}${apiPrefix}/:path*`,
      },
    ];
  },
};

export default nextConfig;
