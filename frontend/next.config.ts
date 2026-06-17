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
  images: {
    remotePatterns: [
      {
        protocol: 'https',
        hostname: '*.s3.*.amazonaws.com',
        pathname: '/**',
      },
    ],
  },
  async rewrites() {
    // Allows seamless API proxying without CORS issues.
    // Defaults to localhost for direct development, but uses the container name for docker-compose.
    const backendUrl = process.env.BACKEND_URL || 'http://127.0.0.1:8000';
    return [
      {
        source: '/api/:path*',
        destination: `${backendUrl}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
