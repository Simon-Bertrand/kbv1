import { authenticateSession } from '../auth';
import { serviceAvailable } from '../readiness';
import { createChatService } from './service';
import { createChatHandlers } from './handlers';
import { createRateLimiter } from '../http/rate-limiter';

const chatRateLimiter = createRateLimiter({scope:'chat-generation',
  rps: Number(process.env.WEBUI_CHAT_RATE_LIMIT_RPS || 1),
  burst: Number(process.env.WEBUI_CHAT_RATE_LIMIT_BURST || 2),
});

// Configuration is evaluated at request time; builds need no deployment secrets.
export function chatHandlers() {
  return createChatHandlers({
    publicOrigin: process.env.WEBUI_AUTH_URL,
    authenticate: authenticateSession,
    service: createChatService({ env: process.env, agentAvailable: () => serviceAvailable('agent') }),
    rateLimiter: chatRateLimiter,
  });
}
