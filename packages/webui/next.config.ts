import { execFileSync } from 'node:child_process';
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
function git(...args: string[]) {
  try {
    return execFileSync('git', args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim();
  } catch {
    return '';
  }
}

export default function configure(phase: string): NextConfig {
  const dockerCommit = process.env.WEBUI_GIT_COMMIT?.trim();
  const commit = dockerCommit || git('rev-parse', 'HEAD');
  return {
    ...config,
    env: {
      WEBUI_BUILD_COMMIT: /^[0-9a-f]{7,64}$/i.test(commit) ? commit : '',
      WEBUI_BUILD_BRANCH: (process.env.WEBUI_GIT_BRANCH?.trim() || (!dockerCommit && git('symbolic-ref', '--quiet', '--short', 'HEAD')) || '').slice(0, 250),
      WEBUI_BUILD_DATE: phase === 'phase-production-build' ? new Date().toISOString() : '',
    },
  };
}
