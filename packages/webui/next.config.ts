import type { NextConfig } from 'next';
const config: NextConfig = {
  output: 'standalone',
  async redirects() {
    return ['/knowledge/:path*', '/knowledge-base/:path*'].map((source) => ({
      source,
      destination: '/',
      permanent: false,
    }));
  },
  poweredByHeader: false,
  reactStrictMode: true,
  experimental: { useTypeScriptCli: false },
};
export default config;
