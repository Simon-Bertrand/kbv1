import { createRateLimiter } from '../http/rate-limiter';
import { getClientIp } from '../logger';
const accountLimiter = createRateLimiter({scope:'login-account',rps:Number(process.env.WEBUI_LOGIN_ACCOUNT_RPS||0.1),burst:Number(process.env.WEBUI_LOGIN_ACCOUNT_BURST||10)});
const loginLimiter = createRateLimiter({scope:'login-ip',rps:Number(process.env.WEBUI_LOGIN_IP_RPS||0.2),burst:Number(process.env.WEBUI_LOGIN_IP_BURST||5)});
import { createHash, timingSafeEqual } from 'node:crypto';

export interface LoginCredentials {
  login: string;
  password: string;
}

export interface CredentialUser {
  /** Stable account identifier from the trusted account store. */
  id: string;
  name?: string | null;
  email?: string | null;
}

export type VerifyCredentials = (
  credentials: Readonly<LoginCredentials>,
  request: Request,
) => Promise<CredentialUser | null>;

function digest(value: string) {
  return createHash('sha256').update(value, 'utf8').digest();
}

/** Server-only administrator account; missing configuration always denies login. */
export function createCredentialsVerifier(env: Record<string, string | undefined>): VerifyCredentials {
  const adminLogin = env.WEBUI_AUTH_ADMIN_LOGIN?.trim();
  const adminPassword = env.WEBUI_AUTH_ADMIN_PASSWORD;
  return async ({ login, password }, request) => {
    try {
      const ip=await loginLimiter.consumeAsync!(getClientIp(request));
      if(!ip.allowed)return null;
      const account=await accountLimiter.consumeAsync!(login.toLowerCase());
      if(!ip.allowed || !account.allowed)return null;
    } catch {return null;}
    if (!adminLogin || adminLogin.length > 256 || !adminPassword?.trim() || adminPassword.length > 1024)
      return null;
    // Fixed-size digests allow constant-time comparison even for different lengths.
    const loginMatches = timingSafeEqual(digest(login), digest(adminLogin));
    const passwordMatches = timingSafeEqual(digest(password), digest(adminPassword));
    return loginMatches && passwordMatches ? { id: adminLogin, name: adminLogin } : null;
  };
}
