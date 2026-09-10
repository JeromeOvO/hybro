import type { NextConfig } from "next";
import { parsePublicConfig } from './src/lib/config-schema'

const publicConfig = parsePublicConfig(process.env.HYBRO_FRONTEND_CONFIG)
const nextConfig: NextConfig = {
  env: {
    HYBRO_FRONTEND_CONFIG: JSON.stringify(publicConfig),
    // SDK interop only: values come from the same validated public projection.
    ...Object.fromEntries(Object.entries(publicConfig).map(([key, value]) => [`NEXT_PUBLIC_${key.toUpperCase()}`, String(value)])),
  },
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
