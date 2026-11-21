import { log, setRequestUser } from '../logger';
import type { NextAuthConfig } from 'next-auth';
import Credentials from 'next-auth/providers/credentials';
import { createHmac } from 'node:crypto';
import { createCredentialsVerifier, type VerifyCredentials } from './credentials';

/** Bridge Auth.js' internal environment names in this WebUI server process only. */
export function configureAuthRuntime(env: Record<string, string | undefined>): void {
  env.AUTH_URL = env.WEBUI_AUTH_URL ?? '';
  env.AUTH_SECRET = env.WEBUI_AUTH_SECRET ?? '';
  // Auth.js must not infer another application's URL, keys or redirect proxy.
  for (const name of ['NEXTAUTH_URL', 'NEXTAUTH_SECRET', 'AUTH_SECRET_1', 'AUTH_SECRET_2',
    'AUTH_SECRET_3', 'AUTH_REDIRECT_PROXY_URL']) delete env[name];
}

/** Read at request time so Docker builds never need authentication secrets. */
export function createAuthConfig(
  env: Record<string, string | undefined>,
  verifyCredentials: VerifyCredentials = createCredentialsVerifier(env),
): NextAuthConfig {
  const issuer = env.WEBUI_AUTH_OIDC_ISSUER;
  const clientId = env.WEBUI_AUTH_OIDC_ID;
  const clientSecret = env.WEBUI_AUTH_OIDC_SECRET;
  const configured = [issuer, clientId, clientSecret].some(Boolean);
  if (configured && (!issuer || !clientId || !clientSecret))
    throw new Error('Configure WEBUI_AUTH_OIDC_ISSUER, WEBUI_AUTH_OIDC_ID and WEBUI_AUTH_OIDC_SECRET together');
  const adminConfigured = Boolean(env.WEBUI_AUTH_ADMIN_LOGIN || env.WEBUI_AUTH_ADMIN_PASSWORD);
  if ((configured || adminConfigured || env.WEBUI_AUTH_SECRET) && (!env.WEBUI_AUTH_SECRET || env.WEBUI_AUTH_SECRET.length < 32))
    throw new Error('WEBUI_AUTH_SECRET must contain at least 32 characters');
  if (issuer) {
    const url = new URL(issuer);
    if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password || url.search || url.hash)
      throw new Error('WEBUI_AUTH_OIDC_ISSUER must be an HTTP(S) issuer URL');
    if (env.NODE_ENV === 'production' && url.protocol !== 'https:')
      throw new Error('WEBUI_AUTH_OIDC_ISSUER must use HTTPS in production');
  }

  return {
    secret: env.WEBUI_AUTH_SECRET,
    basePath: '/api/auth',
    // Enable only when the ingress controls Host / X-Forwarded-* headers.
    trustHost: env.WEBUI_AUTH_TRUST_HOST === 'true',
    session: { strategy: 'jwt', maxAge: 8 * 60 * 60 },
    pages: { signIn: '/login', error: '/login' },
    providers: [Credentials({
      credentials: { login: { type: 'text' }, password: { type: 'password' } },
      async authorize(credentials, request) {
        const { login, password } = credentials;
        if (typeof login !== 'string' || typeof password !== 'string' ||
            !login.trim() || login.length > 256 || !password || password.length > 1024)
          return null;
        const user = await verifyCredentials({ login: login.trim(), password }, request);
        return user && typeof user.id === 'string' && user.id ? user : null;
      },
    }), ...(configured ? [{
      id: 'oidc',
      name: 'OIDC',
      type: 'oidc' as const,
      issuer,
      clientId,
      clientSecret,
      authorization: { params: { scope: 'openid profile email' } },
      checks: ['pkce', 'state'] as ('pkce' | 'state')[],
    }] : [])],
    logger: {
      error(error) { log('ERROR', 'Authentication error', { logger: 'auth', error_type: error.name }); },
      warn(code) { log('WARNING', String(code), { logger: 'auth' }); },
      debug() {},
    },
    callbacks: {
      jwt({ token, account, user }) {
        const epoch = env.WEBUI_SESSION_EPOCH ?? '0';
        const credentialVersion = createHmac('sha256',env.WEBUI_AUTH_SECRET ?? '').update(JSON.stringify([env.WEBUI_AUTH_ADMIN_LOGIN,env.WEBUI_AUTH_ADMIN_PASSWORD,epoch])).digest('hex');
        const disabled = (env.WEBUI_DISABLED_SUBJECTS ?? '').split('\n').filter(Boolean);
        if (!account && token.sessionEpoch !== epoch) return null;
        if (!account && typeof token.sub==='string' && disabled.includes(token.sub)) return null;
        if (!account && token.credentialVersion !== undefined && token.credentialVersion !== credentialVersion) return null;
        token.sessionEpoch = epoch;
        if (account?.provider==='credentials') token.credentialVersion=credentialVersion;
        // Bind identity to issuer + subject, never to a mutable email address.
        if (account?.provider === 'credentials') token.sub = JSON.stringify(['credentials', user.id]);
        else if (account) token.sub = JSON.stringify([issuer, account.providerAccountId]);
        if (account) setRequestUser({ id: token.sub, name: user?.name, email: user?.email });
        return token;
      },
      session({ session, token }) {
        session.user.id = token.sub ?? '';
        return session;
      },
    },
  };
}
