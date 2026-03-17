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
};

export default nextConfig;
