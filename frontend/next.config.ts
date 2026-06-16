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
};

export default nextConfig;
