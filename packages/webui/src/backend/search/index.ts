import { authenticateSession } from '../auth';
import { createSearchService } from './service';
import { createSearchHandlers } from './handlers';
import { createRateLimiter } from '../http/rate-limiter';

const searchRateLimiter = createRateLimiter({scope:'search-generation',
  rps: Number(process.env.WEBUI_SEARCH_RATE_LIMIT_RPS || 1),
  burst: Number(process.env.WEBUI_SEARCH_RATE_LIMIT_BURST || 3),
});

export function searchHandlers() {
  return createSearchHandlers({
    authenticate: authenticateSession,
    publicOrigin: process.env.WEBUI_AUTH_URL,
    service: createSearchService({ env: process.env }),
    rateLimiter: searchRateLimiter,
  });
}
