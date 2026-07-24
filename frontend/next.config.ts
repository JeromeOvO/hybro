import type { NextConfig } from "next";

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
    const backendUrl = process.env.BACKEND_URL || 'http://127.0.0.1:8000';
    const configuredPrefix = process.env.NEXT_PUBLIC_API_PREFIX || '/api/v1';
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
